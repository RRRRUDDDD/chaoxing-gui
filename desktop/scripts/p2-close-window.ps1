param([Parameter(Mandatory=$true)][int]$HostProcessId)
$ErrorActionPreference='Stop'
$p2WindowTitle=(Get-Content -Raw -LiteralPath "$PSScriptRoot/../src-tauri/tauri.conf.json" | ConvertFrom-Json).app.windows[0].title
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class P2WindowClose {
    public delegate bool WindowCallback(IntPtr handle, IntPtr extra);
    [DllImport("user32.dll")] public static extern bool EnumWindows(WindowCallback callback, IntPtr extra);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr handle, out uint process);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr handle, uint message, IntPtr wparam, IntPtr lparam);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr handle, StringBuilder text, int max);
    public static int Close(uint target, string title) {
        int count=0;
        EnumWindows((handle, extra) => {
            uint process; GetWindowThreadProcessId(handle, out process);
            if (process==target) {
                var text = new StringBuilder(512);
                GetWindowText(handle, text, text.Capacity);
                // Tauri/COM also own hidden dispatcher windows. Sending those
                // WM_CLOSE corrupts teardown; emulate only the main close button.
                if (text.ToString()==title && PostMessage(handle, 0x0010, IntPtr.Zero, IntPtr.Zero)) count++;
            }
            return true;
        }, IntPtr.Zero);
        return count;
    }
}
'@
$closed=[P2WindowClose]::Close($HostProcessId, $p2WindowTitle)
if ($closed -eq 0) { throw "No window belongs to test host PID $HostProcessId" }
Write-Output "Sent WM_CLOSE to $closed window(s) of test host $HostProcessId"
