param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string] $Title,
    [Parameter(Mandatory = $true, Position = 1)]
    [string] $Body
)

$ErrorActionPreference = 'Stop'

try {
    $null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
    $null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]

    $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $textNodes = $template.GetElementsByTagName('text')
    [void] $textNodes.Item(0).AppendChild($template.CreateTextNode($Title))
    [void] $textNodes.Item(1).AppendChild($template.CreateTextNode($Body))
    $aumid = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
    $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($aumid)
    $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
    $notifier.Show($toast)
    exit 0
}
catch {
    exit 1
}
