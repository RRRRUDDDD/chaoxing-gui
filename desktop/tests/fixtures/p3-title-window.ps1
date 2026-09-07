# Invisible real HWND used to verify exact-PID/Unicode-title WM_CLOSE.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
$p3Window = [Windows.Forms.Form]::new()
try {
    $p3Window.Text = '超星学习通 · 自动化学习助手'
    $p3Window.ShowInTaskbar = $false
    $p3Window.Opacity = 0
    $p3Window.Add_Shown({ [Console]::WriteLine('window-ready') })
    $null = $p3Window.ShowDialog()
    [Console]::WriteLine('window-closed')
} finally { $p3Window.Dispose() }
