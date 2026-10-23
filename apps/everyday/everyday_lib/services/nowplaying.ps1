# Streams the current Windows media session as one JSON line per interval. Needs Windows PowerShell 5.1
# (powershell.exe), which can reach WinRT's GlobalSystemMediaTransportControlsSessionManager without
# extra modules - Python's winrt packages have no builds for every Python version.
# Usage: powershell.exe -NoProfile -ExecutionPolicy Bypass -File nowplaying.ps1 [-IntervalMs 1000] [-Count 0]
param([int]$IntervalMs = 1000, [int]$Count = 0, [int]$ParentPid = 0)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $task.Wait(-1) | Out-Null
    $task.Result
}
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
$mgrType = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]
$propType = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties]
$mgr = Await ($mgrType::RequestAsync()) $mgrType
$n = 0
while ($Count -eq 0 -or $n -lt $Count) {
    $n++
    # Exit with the app that started us (also covers crashes, where nothing kills us explicitly).
    if ($ParentPid -gt 0 -and -not (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue)) { exit }
    try {
        $s = $mgr.GetCurrentSession()
        if ($null -eq $s) {
            '{"active":false}'
        } else {
            $p = Await ($s.TryGetMediaPropertiesAsync()) $propType
            $info = $s.GetPlaybackInfo()
            $tl = $s.GetTimelineProperties()
            [ordered]@{
                active = $true; app = $s.SourceAppUserModelId; title = $p.Title; artist = $p.Artist
                album = $p.AlbumTitle; status = $info.PlaybackStatus.ToString()
                position = [math]::Round($tl.Position.TotalSeconds); duration = [math]::Round($tl.EndTime.TotalSeconds)
            } | ConvertTo-Json -Compress
        }
    } catch {
        '{"active":false,"error":' + ($_.Exception.Message | ConvertTo-Json -Compress) + '}'
    }
    [Console]::Out.Flush()
    Start-Sleep -Milliseconds $IntervalMs
}
