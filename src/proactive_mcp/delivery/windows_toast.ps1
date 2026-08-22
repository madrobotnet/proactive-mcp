param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string] $Title,
    [Parameter(Mandatory = $true, Position = 1)]
    [string] $Body
)

$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
$null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]

$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$textNodes = $template.GetElementsByTagName('text')
[void] $textNodes.Item(0).AppendChild($template.CreateTextNode($Title))
[void] $textNodes.Item(1).AppendChild($template.CreateTextNode($Body))
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier()
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
$notifier.Show($toast)
