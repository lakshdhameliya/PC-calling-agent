$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    $speaker = New-Object -ComObject SAPI.SpVoice
    @{ type = 'ready'; voices = @(); selected = 'SAPI.SpVoice' } | ConvertTo-Json -Compress | ForEach-Object { [Console]::WriteLine($_) }
    while ($null -ne ($line = [Console]::ReadLine())) {
        try {
            $message = $line | ConvertFrom-Json
            switch ($message.action) {
                { $_ -in 'speak', 'cache' } {
                    $path = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), 'qwen_voice_' + [System.Guid]::NewGuid().ToString('N') + '.wav')
                    $stream = New-Object -ComObject SAPI.SpFileStream
                    $stream.Open($path, 3, $false)
                    $speaker.AudioOutputStream = $stream
                    [void]$speaker.Speak([string]$message.text)
                    $stream.Close()
                    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($stream) | Out-Null
                    @{ type = 'wav'; path = $path; text = [string]$message.text; purpose = $(if ($message.action -eq 'cache') { 'cache' } else { 'play' }); token = [int]$message.token } | ConvertTo-Json -Compress | ForEach-Object { [Console]::WriteLine($_) }
                }
                'stop' {
                    @{ type = 'stopped' } | ConvertTo-Json -Compress | ForEach-Object { [Console]::WriteLine($_) }
                }
                'close' { break }
            }
            if ($message.action -eq 'close') { break }
        } catch {
            [Console]::WriteLine((@{ type = 'error'; message = $_.Exception.Message } | ConvertTo-Json -Compress))
        }
    }
} catch {
    [Console]::WriteLine((@{ type = 'error'; message = $_.Exception.Message } | ConvertTo-Json -Compress))
} finally {
    if ($speaker) { [System.Runtime.InteropServices.Marshal]::ReleaseComObject($speaker) | Out-Null }
}
