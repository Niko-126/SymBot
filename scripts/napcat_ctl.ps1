param(
    [Parameter(Mandatory = $true)][string]$Action,
    [string]$Out = "",
    [string]$Text = "",
    [int]$TargetPid = 0
)

Add-Type -AssemblyName System.Windows.Forms, System.Drawing

$sig = @'
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class WinCtl {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr hWnd, uint uCmd);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
    public const uint GW_HWNDNEXT = 2;
    public static List<string> ListWindows() {
        List<string> res = new List<string>();
        IntPtr h = IntPtr.Zero;
        while ((h = GetWindow(h, GW_HWNDNEXT)) != IntPtr.Zero) {
            if (!IsWindowVisible(h)) continue;
            StringBuilder sb = new StringBuilder(512);
            GetWindowText(h, sb, 512);
            uint pid; GetWindowThreadProcessId(h, out pid);
            if (sb.Length == 0) continue;
            res.Add(pid + "|" + h.ToInt64() + "|" + sb.ToString());
        }
        return res;
    }
}
'@
Add-Type -TypeDefinition $sig

switch ($Action) {
    "list" {
        [WinCtl]::ListWindows() | ForEach-Object { Write-Output $_ }
    }
    "shot" {
        if (-not $Out) { $Out = Join-Path $env:TEMP "napcat-shot.png" }
        $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($b.X, $b.Y, 0, 0, $bmp.Size)
        $bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
        $g.Dispose()
        $bmp.Dispose()
        Write-Output $Out
    }
    "keys" {
        if ($TargetPid -gt 0) {
            $p = [System.Diagnostics.Process]::GetProcessById($TargetPid)
            $h = $p.MainWindowHandle
            if ($h -ne [IntPtr]::Zero) {
                [WinCtl]::ShowWindow($h, 9) | Out-Null
                [WinCtl]::SetForegroundWindow($h) | Out-Null
                Start-Sleep -Milliseconds 500
            }
        }
        foreach ($ch in $Text.ToCharArray()) {
            $v = [int][char]$ch
            if ($v -eq 10) { continue }
            $vk = if ($v -eq 13) { 0x0D } else { $v }
            [WinCtl]::keybd_event([byte]$vk, 0, 0, [UIntPtr]::Zero)
            Start-Sleep -Milliseconds 40
            [WinCtl]::keybd_event([byte]$vk, 0, 2, [UIntPtr]::Zero)
            Start-Sleep -Milliseconds 70
        }
        Write-Output "sent $($Text.Length) chars"
    }
    default { Write-Output "unknown action: $Action" }
}
