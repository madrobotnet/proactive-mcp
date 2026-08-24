# 메모리 모델 v2 — entity·계층·중복 병합

> **상태:** Owner 승인 (2026-08-21), 구현 대기
> **대체 대상:** [`PRODUCT_PLAN.md`](PRODUCT_PLAN.md) §8 메모리 모델
> **착수 시점:** M2 머지 직후 ~ M3 착수 전 (§7 참조)

## 1. 왜 지금 바꾸나

M1의 `memory_items`는 단일 테이블에 `kind` enum과 자유 텍스트 `entity`만 있다. 항목이 늘면 세 가지가 무너진다.

1. **중복이 알림 중복으로 이어진다.** `remember`는 중복 검사 없이 INSERT하므로 에이전트가 세션마다 같은 사실을 다시 저장한다. "엄마 생신 7/18"이 10건 쌓이면 §6.3 `personal_occasion`의 dedupe key가 "memory item id + occurrence 연도"라서 **생일 하나에 알림 10개**가 된다. 이건 정리 문제가 아니라 M3 정확성 버그다.
2. **동일 대상을 묶을 수단이 없다.** `entity`가 자유 텍스트라 "엄마"로 저장하고 "어머니"로 물으면 못 찾는다. 한 인물에 대한 정보를 모아 보거나 통째로 지울 수도 없다.
3. **recall이 컨텍스트를 잡아먹는다.** `LIMIT`이 없고 정렬이 `id ASC`(오래된 것 우선)라, 매칭 200건이면 200건이 전부 에이전트 응답에 실린다.

M3의 감지기들이 메모리 구조를 읽기 시작하면 감지기·dedupe key·테스트를 함께 고쳐야 하므로 비용이 급증한다. **지금이 유일하게 싼 구간이다.**

## 2. 원본 World Model에서 무엇을 가져오고 무엇을 버리나

`hermes-proactive`의 World Model은 초기 스키마 72개 테이블(지식·메모리 관련 29개), `knowledge_repository.py` 90KB, `memory_repository.py` 98KB 규모다. 구조는 Entity → Claim → Fact(리비전) → Conflict 4단이다.

**중요한 사실: 원본에도 분류 계층은 없었다.** `entity_kind`는 평면 enum 8개였다. 원본의 정교함은 계층이 아니라 (a) `entity_aliases`로 동일 대상 식별, (b) `predicate_registry`로 통제된 속성 어휘, (c) 권위·모순 판정에서 나왔다.

### 가져오는 것

| 원본 | v2에서의 형태 |
|---|---|
| `entities` + `entity_kind` | 5종 고정 enum (원본의 provider 전용 종류 제외) |
| `entity_aliases` | 그대로 채택. "엄마/어머니/모친" 문제의 유일한 해법 |
| `predicate_registry` | 경량화 — 레지스트리 테이블 없이 `attribute` 고정 enum |
| 모순 보존 원칙 | 유지. 단 별도 `conflicts` 테이블 없이 자연키와 플래그로 처리 |

### 버리는 것 (개발 에이전트는 이 목록을 확대 해석하지 말 것)

- **필드 단위 AEAD 암호화 + blind index** — M1.5에서 파일 단위 DACL/0600 보호를 검증했다. 그것으로 충분하다
- **profile 스코프** — 단일 사용자 제품이다
- **`confidence_ppm`, 6단계 authority** — 출처가 `agent_conversation`/`manual` 둘뿐이라 등급이 무의미하다
- **bitemporal 유효기간(`valid_from_us`/`valid_to_us`), `fact_revisions` 리비전 번호**
- **claims/facts 테이블 분리** — 단일 테이블 + status
- **7단계 상태 머신** — `active`/`superseded`/`archived` 3개

## 3. 두 축 분리

M1의 `kind`는 두 축을 섞고 있었다. `person_fact`만 "대상 종류 × 진술 종류"였고 나머지는 진술 종류뿐이라 "장소에 대한 사실"을 넣을 자리가 없었다. v2는 축을 분리한다.

- **무엇에 관한 것인가** → `entities.kind` (고정 5종) + `entities.path` (자유 계층)
- **어떤 종류의 진술인가** → `memory_items.kind` (fact / commitment / preference / note)
- **어떤 속성인가** → `memory_items.attribute` (고정 enum, predicate 경량판)

### 1차 카테고리 (닫힌 집합)

```
person   사람
place    장소
org      단체·회사
thing    물건
activity 프로젝트·활동   ← "개발 프로젝트 마감일"처럼 활동 자체가 주어인 경우
```

`other`는 두지 않는다. 잡동사니 서랍이 되어 LLM이 남용한다. 주어가 애매한 메모는 `entity_id`를 NULL로 둔다.

### 2차 이하 = 생활 영역 (열린 집합)

`path`의 첫 단이 생활 영역을 담는다. 사람마다 다르고 계속 늘어나므로 자유 텍스트다.

```
person   / 가족/어머니
person   / 직장/팀장
org      / 직장/현재회사
thing    / 쇼핑/러닝화
activity / 개발/proactive-mcp
```

`path LIKE '개발/%'`는 **kind를 가로질러** 조회된다. 즉 "개발 관련 전부"와 "사람 전부" 두 가지 시각을 모두 얻는다.

정규화 규칙: 유니코드 NFC 통일, 앞뒤 공백 제거, 빈 세그먼트 금지, 최대 3단, 구분자 `/`.

## 4. 스키마

마이그레이션 번호는 M2가 사용하는 번호 다음으로 잡는다 (§7 참조). SQLite `ALTER TABLE ADD COLUMN`은 제약이 있으므로 `memory_items`는 **새 테이블 생성 → 복사 → 교체** 방식을 쓴다. 기존 데이터는 스모크 1건 수준이라 비용이 없다.

```sql
CREATE TABLE entities (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL CHECK(kind IN ('person','place','org','thing','activity')),
    path        TEXT,                     -- '가족/어머니' (정규화됨, 최대 3단)
    label       TEXT NOT NULL,            -- 대표 이름
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active','merged','archived')),
    merged_into INTEGER REFERENCES entities(id),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX idx_entities_kind_path ON entities(kind, path);
CREATE INDEX idx_entities_path      ON entities(path);

CREATE TABLE entity_aliases (
    id         INTEGER PRIMARY KEY,
    entity_id  INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    alias      TEXT NOT NULL,             -- 원문 ("어머니")
    alias_norm TEXT NOT NULL,             -- NFC + 소문자 + 공백 제거
    source     TEXT NOT NULL,             -- agent_conversation | manual
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX uq_entity_alias_norm ON entity_aliases(alias_norm);
```

`alias_norm`을 전역 unique로 둔다. 그래야 `remember`가 별칭만 보고 **결정론적으로** entity를 찾을 수 있다. 같은 별칭을 다른 대상에 붙이려 하면 도구가 오류를 반환하고 더 구체적인 이름이나 `entity_id`를 쓰도록 안내한다 — 조용히 잘못 연결하는 것보다 낫다.

`memory_items` 변경:

```sql
kind        TEXT NOT NULL CHECK(kind IN ('fact','commitment','preference','note')),
entity_id   INTEGER REFERENCES entities(id),   -- NULL 허용 (주어 없는 메모)
attribute   TEXT NOT NULL DEFAULT 'free'
            CHECK(attribute IN ('birthday','anniversary','deadline','relationship','free')),
supersedes_id INTEGER REFERENCES memory_items(id),
-- 기존 컬럼(content, date_anchor, recurrence, lead_days, source, created_at,
--          updated_at, archived)은 유지
```

기존 `person_fact` → `fact`로 매핑한다.

`attribute`를 레지스트리 대신 고정 enum으로 두는 근거: **M3의 메모리 기반 상황은 `personal_occasion` 하나뿐이고 `date_anchor` + `recurrence`만 소비한다.** 날짜가 붙는 자리만 구조를 조이고 나머지는 `free`로 느슨하게 둔다.

### 중복 병합과 모순 보존

```sql
CREATE UNIQUE INDEX uq_memory_dated_fact
    ON memory_items(entity_id, attribute, date_anchor)
    WHERE archived = 0 AND attribute <> 'free';
```

이 인덱스 하나가 두 요구를 동시에 만족한다.

- **같은 사실 재저장** — `(entity_id, attribute, date_anchor)`가 같으면 새 행을 만들지 않고 `updated_at`만 갱신한다. 중복 알림이 사라진다
- **모순** — 같은 `(entity_id, attribute)`인데 `date_anchor`가 다르면 자연키가 다르므로 **두 행이 모두 남는다**. 기획서 §8의 "덮어쓰지 않고 둘 다 보존" 원칙 그대로다. `recall`은 이 경우를 감지해 결과에 모순 플래그를 붙여 노출한다

## 5. 도구 변경

| 도구 | 변경 |
|---|---|
| `remember` | `entity`(이름), `entity_kind`, `entity_path`, `attribute` 인자 추가. 별칭 정확 일치로 기존 entity에 자동 연결하고, 없으면 생성하면서 그 이름을 별칭으로 등록 |
| `recall` | `path_prefix`, `entity_kind`, `limit`(기본 20) 추가. 정렬을 **최신 우선**으로 변경. 모순 항목에 플래그 |
| `update` | **신규.** 지금은 수정 경로가 없어 forget + remember를 반복하며 아카이브 행이 쌓인다 |
| `list_entities` | **신규.** `kind`/`path_prefix`로 조회하고 `after_id`/`next_after_id` 커서로 전체 결과를 순회. 에이전트가 새 경로를 발명하기 전에 기존 분류를 보게 하는 장치 |
| `forget` | 변경 없음 (소프트 아카이브 유지) |

### LLM 분류 드리프트 방지

계층의 최대 위험은 에이전트가 `가족/어머니`, `가족/엄마`, `모친`을 제각각 만드는 것이다. 세 겹으로 막는다.

1. 1차 카테고리는 CHECK 제약으로 강제되는 닫힌 집합이라 벗어날 수 없다
2. `path`는 저장 시 정규화한다 (NFC, 공백, 빈 세그먼트, 깊이)
3. 도구 설명에 "새 경로를 만들기 전에 `list_entities`로 기존 경로를 먼저 확인하라"를 명시한다

## 6. 범위 밖 (별도 백로그)

- **FTS5 검색 고도화** — 이 프로젝트의 SQLite는 3.50.4로 FTS5를 지원하지만, trigram 토크나이저는 **3글자 미만 질의를 매칭하지 못한다**. 한국어는 2음절 단어가 흔해("엄마", "생신", "미팅") 단순 교체는 퇴보다. 짧은 질의는 LIKE, 3글자 이상은 FTS5 BM25로 나누는 하이브리드가 필요하며 별도 과제로 다룬다
- **임베딩 검색** — 호스팅 임베딩 API 사용은 §9.6 "외부 전송 금지" 불변식 위반이다. 로컬 모델은 무거운 의존성을 수반하므로 가치가 입증될 때까지 보류한다
- **`commitment` 자동 만료** — 기한 지난 약속의 자동 아카이브. 이 마일스톤에 넣어도 되지만 우선순위는 낮다

## 7. 착수 시점과 충돌 주의

- **M2 머지 후 착수한다.** M2가 sync 상태 테이블로 다음 마이그레이션 번호를 쓸 가능성이 높아, 먼저 시작하면 번호 충돌과 `store/__init__.py` 리베이스 충돌이 난다
- **M3 착수 전에 끝낸다.** M3 감지기가 메모리 구조를 읽기 시작하면 감지기·dedupe key·테스트를 함께 고쳐야 한다

## 8. 완료 기준

- [ ] `entities`·`entity_aliases` 테이블과 `memory_items` 재구성 마이그레이션, 기존 행 무손실 이관
- [ ] 별칭 자동 연결: "엄마"로 저장 후 "어머니"로 `recall` 시 회수 (hermetic 테스트)
- [ ] 같은 사실 재저장 시 행이 늘지 않음, `date_anchor`가 다르면 두 행 모두 보존되고 모순 플래그 노출 (hermetic 테스트)
- [ ] `path_prefix` 조회가 kind를 가로질러 동작
- [ ] `recall` 기본 `limit` 적용과 최신 우선 정렬
- [ ] `update`·`list_entities` 도구 동작
- [ ] 경로 정규화 (NFC/공백/빈 세그먼트/깊이 3) 경계 테스트
- [ ] Linux·Windows·macOS CI green
