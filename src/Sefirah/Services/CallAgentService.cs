using System.Diagnostics;
using System.Net.Http;

namespace Sefirah.Services;

/// <summary>Starts and stops the bundled LakshVoiceAgent process from Frontbit Link.</summary>
public sealed class CallAgentService
{
    private static string ResolveRoot()
    {
        var candidates = new[]
        {
            Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "VoiceAgent", "AIPhoneVoiceBot_NoADB_POC"),
            Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, @"..\..\..\..\VoiceAgent\AIPhoneVoiceBot_NoADB_POC")),
            @"C:\Frontbit Link\VoiceAgent\AIPhoneVoiceBot_NoADB_POC"
        };
        foreach (var candidate in candidates)
        {
            if (Directory.Exists(candidate))
                return candidate;
        }
        return candidates[0];
    }

    private static string ResolvePython(bool pythonw = false)
    {
        var exeName = pythonw ? "pythonw.exe" : "python.exe";
        var candidates = new[]
        {
            Path.Combine(Root, @"..\venv\Scripts", exeName),
            Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "VoiceAgent", "venv", "Scripts", exeName),
            Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "venv", "Scripts", exeName),
            Path.Combine(@"C:\Frontbit Link\VoiceAgent\venv\Scripts", exeName)
        };
        foreach (var c in candidates)
        {
            if (File.Exists(c)) return c;
        }
        return exeName;
    }

    private static readonly string Root = ResolveRoot();
    private Process? botProcess;
    private static Process? dashboardProcess;
    private static readonly object DashboardLock = new();
    private static readonly Uri DashboardUri = new("http://127.0.0.1:8765/");

    public bool IsRunning => botProcess is { HasExited: false };

    public bool Start()
    {
        if (IsRunning) return true;
        var python = ResolvePython(pythonw: false);
        var script = Path.Combine(Root, "frontbit_link_bot.py");
        if (!File.Exists(python) || !File.Exists(script)) return false;

        botProcess = Process.Start(new ProcessStartInfo
        {
            FileName = python,
            Arguments = "-u frontbit_link_bot.py --fast-start",
            WorkingDirectory = Root,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = false,
            RedirectStandardError = false,
        });
        return botProcess is not null;
    }

    public void Stop()
    {
        if (botProcess is { HasExited: false })
        {
            try { botProcess.Kill(entireProcessTree: true); } catch { }
        }
        botProcess?.Dispose();
        botProcess = null;
    }

    public void OpenDashboard() => StartPython("faq_voice_start_page.py", pythonw: true);
    public async Task<bool> StartDashboardServerAsync(CancellationToken cancellationToken = default)
    {
        var python = ResolvePython(pythonw: false);
        var script = Path.Combine(Root, "faq_voice_start_page.py");
        if (!File.Exists(python) || !File.Exists(script))
            return false;

        try
        {
            lock (DashboardLock)
            {
                if (dashboardProcess is null || dashboardProcess.HasExited)
                {
                    dashboardProcess?.Dispose();
                    dashboardProcess = Process.Start(new ProcessStartInfo
                    {
                        FileName = python,
                        Arguments = "-u faq_voice_start_page.py --server 8765",
                        WorkingDirectory = Root,
                        UseShellExecute = false,
                        CreateNoWindow = true,
                    });
                }
            }

            using var client = new HttpClient { Timeout = TimeSpan.FromMilliseconds(750) };
            for (var attempt = 0; attempt < 40; attempt++)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (dashboardProcess is null || dashboardProcess.HasExited)
                    return false;

                try
                {
                    using var response = await client.GetAsync(DashboardUri, cancellationToken);
                    if (response.IsSuccessStatusCode)
                        return true;
                }
                catch (HttpRequestException) { }
                catch (TaskCanceledException) when (!cancellationToken.IsCancellationRequested) { }

                await Task.Delay(250, cancellationToken);
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch
        {
            return false;
        }

        return false;
    }

    public Uri GetDashboardUri() => DashboardUri;
    public void OpenLiveChat() => StartPython("live_chat_viewer.py", pythonw: true);

    private static void StartPython(string script, bool pythonw)
    {
        var executable = ResolvePython(pythonw);
        if (!File.Exists(executable) || !File.Exists(Path.Combine(Root, script))) return;
        Process.Start(new ProcessStartInfo
        {
            FileName = executable,
            Arguments = script,
            WorkingDirectory = Root,
            UseShellExecute = false,
            CreateNoWindow = true,
        });
    }
}
