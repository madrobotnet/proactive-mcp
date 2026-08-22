import subprocess

out = subprocess.run(
    [
        "gh", "api",
        "repos/madrobotnet/proactive-mcp/issues/comments/5380823691",
        "--jq", ".body",
    ],
    capture_output=True,
    check=True,
)
text = out.stdout.decode("utf-8")
print("has 스모크:", "스모크" in text)
print("has 비결정적:", "비결정적" in text)
print("has replacement char:", "\ufffd" in text)
print("suspicious ??:", "??" in text.replace("???", ""))
print(repr(text[:80]))
