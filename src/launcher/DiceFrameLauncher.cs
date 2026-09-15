using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Security;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using Microsoft.Win32;

internal static class DiceFrameLauncher
{
    private const string DefaultPort = "18000";
    private const int ReadyTimeoutSeconds = 60;
    private const int ProbationDefaultSeconds = 90;
    private const int ProbationMinSeconds = 10;
    private const int ProbationMaxSeconds = 600;
    private const int ProbationFailureGraceSeconds = 20;
    // WER per-app 配置按 exe 名匹配；bundled interpreter 也叫 python.exe，
    // 因此只在 DiceFrame 生命周期内临时写入，退出时必须恢复用户原值。
    private const string LocalDumpsPythonKey =
        @"Software\Microsoft\Windows\Windows Error Reporting\LocalDumps\python.exe";
    private const int MaxCrashDumps = 3;
    // 异常退出后等待 WER 写 dump 的窗口（秒）：过短会丢掉本来能拿到的 dump。
    private const int CrashDumpWaitSeconds = 8;
    private static Process serverProcess;
    private static bool shuttingDown;
    // 异常退出取证：记录当前 server 的启动信息；主动停止（更新/关机）不报 crash。
    private static DateTime serverStartedAtUtc = DateTime.MinValue;
    private static int serverPid;
    private static bool suppressCrashReport;
    // 临时 WER 配置快照：finally 与 ProcessExit 都可能触发恢复，只恢复一次。
    private static CrashDumpRegistrySnapshot crashDumpSnapshot;
    // 本机 DiceFrame endpoint 解析结果：数据目录 + 端口 → scheme 与自签指纹。
    private static string launcherDataDir = "";
    private static string launcherPort = DefaultPort;
    // 仅对 Launcher → 本机 DiceFrame 生效的证书指纹固定；绝不做全局校验关闭。
    private static string expectedCertFingerprint = "";
    private static HttpClient localHttpClient;

    [STAThread]
    private static int Main(string[] args)
    {
        Console.Title = "DiceFrame";

        string installRoot = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar
        );
        string dataDir = Path.Combine(installRoot, "data");
        string logsDir = Path.Combine(installRoot, "logs");
        string updaterDir = Path.Combine(dataDir, "_updater");
        string restartSignal = Path.Combine(updaterDir, "restart_signal.json");
        string currentPointer = Path.Combine(updaterDir, "current.json");

        Directory.CreateDirectory(dataDir);
        Directory.CreateDirectory(logsDir);
        Directory.CreateDirectory(updaterDir);
        TryDelete(Path.Combine(installRoot, "DiceFrame.exe.old"));

        string port = ResolvePort(Path.Combine(dataDir, "config.json"));
        launcherDataDir = dataDir;
        launcherPort = port;
        InitLocalHttpClient();
        string url = ResolveServerEndpoint().Url;
        string activeDir = ResolveActiveDirectory(installRoot, currentPointer);
        MigrateLegacyPortablePayload(
            installRoot,
            currentPointer,
            restartSignal,
            activeDir
        );

        Console.WriteLine("========================================");
        Console.WriteLine("  DiceFrame Portable");
        Console.WriteLine("  " + url);
        Console.WriteLine("========================================");
        Console.WriteLine();

        AppDomain.CurrentDomain.ProcessExit += delegate
        {
            // 进程退出兜底：即使走不到 Main 的 finally，也尽量恢复用户 WER 配置。
            StopServer();
            TryRestoreCrashDumpCapture();
        };
        Console.CancelKeyPress += delegate(object sender, ConsoleCancelEventArgs eventArgs)
        {
            eventArgs.Cancel = true;
            shuttingDown = true;
            StopServer();
            // 不调用 Environment.Exit：那会跳过 Main 的 finally，导致临时
            // WER 配置残留；这里让主循环自然结束并由 finally 恢复。
        };

        try
        {
            // 崩溃取证失败绝不能阻止 DiceFrame 启动（内部只打印提示）。
            crashDumpSnapshot = EnableCrashDumpCapture(logsDir);
            return RunPortable(
                installRoot,
                dataDir,
                logsDir,
                updaterDir,
                restartSignal,
                currentPointer,
                url,
                activeDir
            );
        }
        finally
        {
            // 正常关闭、python crash、更新重启、启动失败都必须恢复用户原配置。
            TryRestoreCrashDumpCapture();
        }
    }

    private static void TryRestoreCrashDumpCapture()
    {
        CrashDumpRegistrySnapshot snapshot = Interlocked.Exchange(
            ref crashDumpSnapshot,
            null
        );
        RestoreCrashDumpCapture(snapshot);
    }

    private static int RunPortable(
        string installRoot,
        string dataDir,
        string logsDir,
        string updaterDir,
        string restartSignal,
        string currentPointer,
        string url,
        string activeDir
    )
    {
        try
        {
            serverProcess = StartServer(installRoot, activeDir, dataDir);
        }
        catch (Exception ex)
        {
            return Fail("DiceFrame failed to start: " + ex.Message);
        }

        if (WaitForServer(serverProcess, url, TimeSpan.FromSeconds(ReadyTimeoutSeconds)))
        {
            OpenBrowser(url);
            Console.WriteLine("DiceFrame is running. Close this window to stop it.");
            Console.WriteLine();
        }
        else if (serverProcess.HasExited)
        {
            // 启动阶段就异常退出（例如 python.exe native crash）同样要留取证：
            // 这类退出不是用户主动关闭，也不属于更新切换。
            if (!suppressCrashReport)
            {
                HandleUnexpectedServerExit(logsDir, serverProcess, activeDir);
                return serverProcess.ExitCode;
            }
            return Fail("DiceFrame exited before the Web UI became ready.");
        }
        else
        {
            Console.WriteLine("DiceFrame is still starting. Open this address manually:");
            Console.WriteLine(url);
            Console.WriteLine();
        }

        while (!shuttingDown)
        {
            if (File.Exists(restartSignal))
            {
                activeDir = HandleUpdate(
                    installRoot,
                    dataDir,
                    updaterDir,
                    currentPointer,
                    restartSignal,
                    activeDir,
                    url
                );
            }

            if (serverProcess == null || serverProcess.HasExited)
            {
                if (serverProcess != null && !suppressCrashReport)
                {
                    HandleUnexpectedServerExit(logsDir, serverProcess, activeDir);
                }
                return serverProcess == null ? 1 : serverProcess.ExitCode;
            }
            Thread.Sleep(500);
        }
        return 0;
    }

    private static string HandleUpdate(
        string installRoot,
        string dataDir,
        string updaterDir,
        string currentPointer,
        string restartSignal,
        string previousDir,
        string url
    )
    {
        // 注意：这里不能提前 suppress——更新信号读取或候选校验失败时会 early
        // return，旧 server 继续正常运行，若提前置位会让后续真实 crash 不再取证。
        // 只有真正准备切换（StopServer 之前）才临时抑制。
        string signal;
        try
        {
            signal = File.ReadAllText(restartSignal, Encoding.UTF8);
        }
        catch (Exception ex)
        {
            WriteUpdateState(
                updaterDir,
                "failed",
                "",
                "无法读取更新重启信号：" + ex.Message
            );
            TryDelete(restartSignal);
            return previousDir;
        }

        string version = JsonString(signal, "expected_version");
        string candidateDir = JsonString(signal, "candidate_dir");
        string launcherPath = JsonString(signal, "launcher_path");
        int probationSeconds = JsonInt(signal, "probation_seconds", ProbationDefaultSeconds);
        probationSeconds = Math.Max(
            ProbationMinSeconds,
            Math.Min(probationSeconds, ProbationMaxSeconds)
        );

        string validationError = ValidateCandidate(
            installRoot,
            candidateDir,
            launcherPath,
            version
        );
        if (validationError != null)
        {
            WriteUpdateState(updaterDir, "failed", version, validationError);
            TryDelete(restartSignal);
            return previousDir;
        }

        Console.WriteLine("Applying DiceFrame " + version + "...");
        // 走到这里才真正开始主动切换：StopServer 与候选/回滚进程的退出由更新
        // 状态记录，不写 last-crash.json；候选 StartServer 会重新打开取证。
        suppressCrashReport = true;
        StopServer();

        Process candidateProcess = null;
        string failure = "";
        try
        {
            candidateProcess = StartServer(installRoot, candidateDir, dataDir);
            serverProcess = candidateProcess;
            // 数据目录是共享的：候选版本启动后重新解析 scheme（HTTP/HTTPS）。
            url = ResolveServerEndpoint().Url;
            if (!WaitForVersion(
                candidateProcess,
                url,
                version,
                TimeSpan.FromSeconds(ReadyTimeoutSeconds)
            ))
            {
                failure = candidateProcess.HasExited
                    ? "候选版本在健康检查前退出"
                    : "候选版本健康检查超时或版本不匹配";
            }
            else if (!PassesProbation(
                candidateProcess,
                url,
                version,
                probationSeconds
            ))
            {
                failure = "候选版本在观察期内退出或失去响应";
            }
        }
        catch (Exception ex)
        {
            failure = "候选版本启动失败：" + ex.Message;
        }

        if (string.IsNullOrEmpty(failure))
        {
            string relativeDir = RelativeVersionDirectory(installRoot, candidateDir);
            string versionsDir = Path.Combine(installRoot, "versions");
            string previousRelativeDir = IsUnder(previousDir, versionsDir)
                ? RelativeVersionDirectory(installRoot, previousDir)
                : "";
            AtomicWriteText(
                currentPointer,
                "{\n"
                    + "  \"schema\": 1,\n"
                    + "  \"version\": \"" + JsonEscape(version) + "\",\n"
                    + "  \"relative_dir\": \"" + JsonEscape(relativeDir) + "\",\n"
                    + "  \"previous_relative_dir\": \""
                    + JsonEscape(previousRelativeDir)
                    + "\"\n"
                    + "}\n"
            );
            PromoteLauncher(installRoot, launcherPath);
            PruneOldVersions(installRoot, candidateDir, previousDir);
            WriteUpdateState(updaterDir, "done", version, "");
            TryDelete(restartSignal);
            Console.WriteLine("Update to " + version + " succeeded.");
            return candidateDir;
        }

        Console.WriteLine("Update failed; rolling back: " + failure);
        StopProcess(candidateProcess);
        try
        {
            serverProcess = StartServer(installRoot, previousDir, dataDir);
            url = ResolveServerEndpoint().Url;
            if (!WaitForServer(serverProcess, url, TimeSpan.FromSeconds(ReadyTimeoutSeconds)))
            {
                WriteUpdateState(
                    updaterDir,
                    "failed",
                    version,
                    failure + "；回滚版本也未能恢复服务"
                );
            }
            else
            {
                WriteUpdateState(updaterDir, "rolled-back", version, failure);
                Console.WriteLine("Rollback succeeded.");
            }
        }
        catch (Exception ex)
        {
            serverProcess = null;
            WriteUpdateState(
                updaterDir,
                "failed",
                version,
                failure + "；回滚启动失败：" + ex.Message
            );
        }
        TryDelete(restartSignal);
        return previousDir;
    }

    private static Process StartServer(
        string installRoot,
        string activeDir,
        string dataDir
    )
    {
        string python = Path.Combine(activeDir, "python", "python.exe");
        string serverScript = Path.Combine(activeDir, "app", "web_server.py");
        if (!File.Exists(python))
        {
            throw new FileNotFoundException("Cannot find bundled Python", python);
        }
        if (!File.Exists(serverScript))
        {
            throw new FileNotFoundException("Cannot find DiceFrame server", serverScript);
        }

        ProcessStartInfo info = new ProcessStartInfo();
        info.FileName = python;
        info.Arguments = Quote(serverScript);
        info.WorkingDirectory = installRoot;
        info.UseShellExecute = false;
        info.EnvironmentVariables["TRPG_DATA_DIR"] = dataDir;
        info.EnvironmentVariables["TRPG_INSTALL_ROOT"] = installRoot;
        info.EnvironmentVariables["TRPG_ACTIVE_VERSION_DIR"] = activeDir;
        Process started = Process.Start(info);
        // 异常退出取证的基准：本次 server 的启动时间与 pid。
        serverStartedAtUtc = DateTime.UtcNow;
        serverPid = started == null ? 0 : started.Id;
        suppressCrashReport = false;
        return started;
    }

    private static string ResolveActiveDirectory(
        string installRoot,
        string currentPointer
    )
    {
        try
        {
            if (File.Exists(currentPointer))
            {
                string json = File.ReadAllText(currentPointer, Encoding.UTF8);
                string current = ResolveVersionDirectory(
                    installRoot,
                    JsonString(json, "relative_dir")
                );
                if (!string.IsNullOrEmpty(current))
                {
                    return current;
                }

                string previous = ResolveVersionDirectory(
                    installRoot,
                    JsonString(json, "previous_relative_dir")
                );
                if (!string.IsNullOrEmpty(previous))
                {
                    Console.WriteLine(
                        "Current version is unavailable; using the previous version."
                    );
                    return previous;
                }
            }
        }
        catch
        {
        }
        return installRoot;
    }

    private static string ResolveVersionDirectory(
        string installRoot,
        string relativeDir
    )
    {
        try
        {
            if (string.IsNullOrEmpty(relativeDir) || Path.IsPathRooted(relativeDir))
            {
                return null;
            }
            string candidate = Path.GetFullPath(Path.Combine(installRoot, relativeDir));
            if (
                IsUnder(candidate, Path.Combine(installRoot, "versions"))
                && HasPortablePayload(candidate)
            )
            {
                return candidate;
            }
        }
        catch
        {
        }
        return null;
    }

    private static bool HasPortablePayload(string directory)
    {
        return File.Exists(Path.Combine(directory, "python", "python.exe"))
            && File.Exists(Path.Combine(directory, "app", "web_server.py"));
    }

    private static void MigrateLegacyPortablePayload(
        string installRoot,
        string currentPointer,
        string restartSignal,
        string activeDir
    )
    {
        try
        {
            if (
                !HasPortablePayload(installRoot)
                || !File.Exists(currentPointer)
                || File.Exists(restartSignal)
            )
            {
                return;
            }

            string pointerJson = File.ReadAllText(currentPointer, Encoding.UTF8);
            string currentRelative = JsonString(pointerJson, "relative_dir");
            string current = ResolveVersionDirectory(installRoot, currentRelative);
            if (
                string.IsNullOrEmpty(current)
                || !string.Equals(
                    Path.GetFullPath(activeDir),
                    Path.GetFullPath(current),
                    StringComparison.OrdinalIgnoreCase
                )
            )
            {
                return;
            }

            string previousRelative = JsonString(
                pointerJson,
                "previous_relative_dir"
            );
            string previous = ResolveVersionDirectory(
                installRoot,
                previousRelative
            );
            if (!string.IsNullOrEmpty(previous))
            {
                if (string.Equals(
                    previous,
                    current,
                    StringComparison.OrdinalIgnoreCase
                ))
                {
                    return;
                }
                DeleteLegacyPayload(Path.Combine(installRoot, "app"));
                DeleteLegacyPayload(Path.Combine(installRoot, "python"));
                return;
            }
            if (!string.IsNullOrEmpty(previousRelative))
            {
                return;
            }

            string updaterState = Path.Combine(
                Path.GetDirectoryName(currentPointer),
                "state.json"
            );
            if (!File.Exists(updaterState))
            {
                return;
            }
            string stateJson = File.ReadAllText(updaterState, Encoding.UTF8);
            string version = JsonString(pointerJson, "version");
            if (
                !string.Equals(
                    JsonString(stateJson, "state"),
                    "done",
                    StringComparison.OrdinalIgnoreCase
                )
                || string.IsNullOrEmpty(version)
                || !string.Equals(
                    JsonString(stateJson, "version"),
                    version,
                    StringComparison.OrdinalIgnoreCase
                )
            )
            {
                return;
            }

            string versionsDir = Path.Combine(installRoot, "versions");
            string inferredPrevious = null;
            foreach (string directory in Directory.GetDirectories(versionsDir))
            {
                string candidate = Path.GetFullPath(directory);
                if (
                    string.Equals(
                        candidate,
                        current,
                        StringComparison.OrdinalIgnoreCase
                    )
                    || !HasPortablePayload(candidate)
                )
                {
                    continue;
                }
                if (!string.IsNullOrEmpty(inferredPrevious))
                {
                    return;
                }
                inferredPrevious = candidate;
            }
            if (string.IsNullOrEmpty(inferredPrevious))
            {
                return;
            }

            AtomicWriteText(
                currentPointer,
                "{\n"
                    + "  \"schema\": 1,\n"
                    + "  \"version\": \"" + JsonEscape(version) + "\",\n"
                    + "  \"relative_dir\": \""
                    + JsonEscape(currentRelative)
                    + "\",\n"
                    + "  \"previous_relative_dir\": \""
                    + JsonEscape(
                        RelativeVersionDirectory(
                            installRoot,
                            inferredPrevious
                        )
                    )
                    + "\"\n"
                    + "}\n"
            );
            DeleteLegacyPayload(Path.Combine(installRoot, "app"));
            DeleteLegacyPayload(Path.Combine(installRoot, "python"));
            Console.WriteLine(
                "Migrated the legacy portable layout to two version slots."
            );
        }
        catch (Exception ex)
        {
            Console.WriteLine(
                "Could not migrate the legacy portable layout: " + ex.Message
            );
        }
    }

    private static string ValidateCandidate(
        string installRoot,
        string candidateDir,
        string launcherPath,
        string version
    )
    {
        if (string.IsNullOrEmpty(version))
        {
            return "重启信号缺少目标版本";
        }
        if (
            string.IsNullOrEmpty(candidateDir)
            || !IsUnder(candidateDir, Path.Combine(installRoot, "versions"))
        )
        {
            return "候选版本目录无效";
        }
        if (
            !File.Exists(Path.Combine(candidateDir, "python", "python.exe"))
            || !File.Exists(Path.Combine(candidateDir, "app", "web_server.py"))
        )
        {
            return "候选版本文件不完整";
        }
        if (
            string.IsNullOrEmpty(launcherPath)
            || !IsUnder(launcherPath, Path.Combine(installRoot, "data", "_updater"))
            || !File.Exists(launcherPath)
        )
        {
            return "候选启动器无效";
        }
        return null;
    }

    private static bool PassesProbation(
        Process process,
        string url,
        string version,
        int seconds
    )
    {
        DateTime deadline = DateTime.UtcNow.AddSeconds(seconds);
        DateTime nextHealthCheck = DateTime.MinValue;
        DateTime failureStarted = DateTime.MinValue;
        TimeSpan failureGrace = TimeSpan.FromSeconds(ProbationFailureGraceSeconds);
        while (DateTime.UtcNow < deadline)
        {
            if (process == null || process.HasExited)
            {
                return false;
            }
            if (DateTime.UtcNow >= nextHealthCheck)
            {
                if (IsHealthyVersion(url, version))
                {
                    failureStarted = DateTime.MinValue;
                }
                else
                {
                    if (failureStarted == DateTime.MinValue)
                    {
                        failureStarted = DateTime.UtcNow;
                    }
                    else if (DateTime.UtcNow - failureStarted >= failureGrace)
                    {
                        return false;
                    }
                }
                nextHealthCheck = DateTime.UtcNow.AddSeconds(2);
            }
            Thread.Sleep(500);
        }
        return true;
    }

    private static bool WaitForServer(
        Process process,
        string url,
        TimeSpan timeout
    )
    {
        DateTime deadline = DateTime.UtcNow.Add(timeout);
        while (DateTime.UtcNow < deadline)
        {
            if (process != null && process.HasExited)
            {
                return false;
            }
            if (CanOpen(url))
            {
                return true;
            }
            Thread.Sleep(500);
        }
        return false;
    }

    private static bool WaitForVersion(
        Process process,
        string url,
        string expectedVersion,
        TimeSpan timeout
    )
    {
        DateTime deadline = DateTime.UtcNow.Add(timeout);
        while (DateTime.UtcNow < deadline)
        {
            if (process != null && process.HasExited)
            {
                return false;
            }
            if (IsHealthyVersion(url, expectedVersion))
            {
                return true;
            }
            Thread.Sleep(500);
        }
        return false;
    }

    private static bool IsHealthyVersion(string baseUrl, string expectedVersion)
    {
        try
        {
            string body = HttpGet(baseUrl + "/api/system/update/health");
            return JsonString(body, "version") == expectedVersion;
        }
        catch
        {
            return false;
        }
    }

    private static bool CanOpen(string url)
    {
        try
        {
            using (
                HttpResponseMessage response = localHttpClient
                    .GetAsync(url, HttpCompletionOption.ResponseHeadersRead)
                    .GetAwaiter()
                    .GetResult()
            )
            {
                int statusCode = (int)response.StatusCode;
                return statusCode >= 200 && statusCode < 500;
            }
        }
        catch
        {
            return false;
        }
    }

    private static string HttpGet(string url)
    {
        using (
            HttpResponseMessage response = localHttpClient
                .GetAsync(url)
                .GetAwaiter()
                .GetResult()
        )
        using (
            StreamReader reader = new StreamReader(
                response.Content.ReadAsStreamAsync().GetAwaiter().GetResult(),
                Encoding.UTF8
            )
        )
        {
            string body = reader.ReadToEnd();
            if ((int)response.StatusCode < 200 || (int)response.StatusCode >= 300)
            {
                throw new WebException("HTTP " + (int)response.StatusCode);
            }
            return body;
        }
    }

    // ---- 本机 endpoint 与证书验证 ----

    private sealed class ServerEndpoint
    {
        public string Url;
        public string Fingerprint;
    }

    /// <summary>
    /// 统一解析本机 DiceFrame endpoint：HTTP 保持旧行为；本地 HTTPS 按指纹文件固定。
    /// WaitForServer、更新健康检查、OpenBrowser 都走这里，不各自拼接 scheme。
    /// </summary>
    private static ServerEndpoint ResolveServerEndpoint()
    {
        string url = "http://127.0.0.1:" + launcherPort;
        string fingerprint = "";
        try
        {
            string configPath = Path.Combine(launcherDataDir, "config.json");
            if (File.Exists(configPath))
            {
                string text = File.ReadAllText(configPath);
                bool selfSigned = Regex
                    .Match(text, "\"tls_mode\"\\s*:\\s*\"self_signed\"")
                    .Success;
                if (selfSigned)
                {
                    string fingerprintPath = Path.Combine(
                        launcherDataDir,
                        "certs",
                        "self-signed",
                        "fingerprint.txt"
                    );
                    if (File.Exists(fingerprintPath))
                    {
                        string recorded = File.ReadAllText(fingerprintPath).Trim();
                        if (recorded.Length > 0)
                        {
                            url = "https://127.0.0.1:" + launcherPort;
                            fingerprint = recorded;
                        }
                    }
                    // 指纹文件缺失（例如服务端回退 HTTP）时保持 http：
                    // 宁可健康检查失败，也不做未验证的 HTTPS 信任。
                }
            }
        }
        catch
        {
        }
        expectedCertFingerprint = fingerprint;
        return new ServerEndpoint { Url = url, Fingerprint = fingerprint };
    }

    private static void InitLocalHttpClient()
    {
        WebRequestHandler handler = new WebRequestHandler();
        // 只影响本机 DiceFrame 请求的 handler；更新、GitHub 等其它流量不走它。
        handler.ServerCertificateValidationCallback = delegate(
            object sender,
            X509Certificate certificate,
            X509Chain chain,
            SslPolicyErrors errors
        )
        {
            return ValidateLocalCertificate(certificate, errors);
        };
        handler.CachePolicy = new System.Net.Cache.RequestCachePolicy(
            System.Net.Cache.RequestCacheLevel.NoCacheNoStore
        );
        localHttpClient = new HttpClient(handler);
        localHttpClient.Timeout = TimeSpan.FromSeconds(2);
    }

    private static bool ValidateLocalCertificate(
        X509Certificate certificate,
        SslPolicyErrors errors
    )
    {
        if (errors == SslPolicyErrors.None)
        {
            // 系统信任链验证通过（未来的 Let's Encrypt 证书走这里）。
            return true;
        }
        // 自签证书：只有指纹与本地记录完全一致时才信任。
        string expected = expectedCertFingerprint;
        if (expected.Length == 0 || certificate == null)
        {
            return false;
        }
        string normalized = expected.Replace(":", "").Replace("-", "").ToUpperInvariant();
        return Sha256Hex(certificate.GetRawCertData()) == normalized;
    }

    private static string Sha256Hex(byte[] data)
    {
        using (SHA256 sha = SHA256.Create())
        {
            byte[] hash = sha.ComputeHash(data);
            StringBuilder builder = new StringBuilder(hash.Length * 2);
            foreach (byte value in hash)
            {
                builder.Append(value.ToString("X2"));
            }
            return builder.ToString();
        }
    }

    private static void PromoteLauncher(string installRoot, string stagedLauncher)
    {
        string launcher = Path.Combine(installRoot, "DiceFrame.exe");
        string oldLauncher = launcher + ".old";
        try
        {
            TryDelete(oldLauncher);
            if (File.Exists(launcher))
            {
                File.Move(launcher, oldLauncher);
            }
            File.Copy(stagedLauncher, launcher, true);
            TryDelete(stagedLauncher);
        }
        catch (Exception ex)
        {
            Console.WriteLine("Launcher replacement deferred: " + ex.Message);
            try
            {
                if (!File.Exists(launcher) && File.Exists(oldLauncher))
                {
                    File.Move(oldLauncher, launcher);
                }
            }
            catch
            {
            }
        }
    }

    private static void WriteUpdateState(
        string updaterDir,
        string state,
        string version,
        string error
    )
    {
        string json = "{\n"
            + "  \"state\": \"" + JsonEscape(state) + "\",\n"
            + "  \"version\": \"" + JsonEscape(version) + "\",\n"
            + "  \"error\": \"" + JsonEscape(error) + "\",\n"
            + "  \"restart_needed\": false,\n"
            + "  \"completed_at\": "
            + DateTimeOffset.UtcNow.ToUnixTimeSeconds().ToString(
                CultureInfo.InvariantCulture
            )
            + "\n}\n";
        AtomicWriteText(Path.Combine(updaterDir, "state.json"), json);
    }

    private static void AtomicWriteText(string path, string content)
    {
        string temp = path + ".tmp";
        string backup = path + ".bak";
        Directory.CreateDirectory(Path.GetDirectoryName(path));
        File.WriteAllText(temp, content, new UTF8Encoding(false));
        if (File.Exists(path))
        {
            TryDelete(backup);
            File.Replace(temp, path, backup, true);
            TryDelete(backup);
        }
        else
        {
            File.Move(temp, path);
        }
    }

    private static string JsonString(string json, string key)
    {
        Match match = Regex.Match(
            json ?? "",
            "\"" + Regex.Escape(key) + "\"\\s*:\\s*\"((?:\\\\.|[^\"])*)\""
        );
        return match.Success ? JsonUnescape(match.Groups[1].Value) : "";
    }

    private static int JsonInt(string json, string key, int fallback)
    {
        Match match = Regex.Match(
            json ?? "",
            "\"" + Regex.Escape(key) + "\"\\s*:\\s*(-?\\d+)"
        );
        int value;
        return match.Success && int.TryParse(match.Groups[1].Value, out value)
            ? value
            : fallback;
    }

    private static string JsonUnescape(string value)
    {
        StringBuilder output = new StringBuilder();
        for (int index = 0; index < value.Length; index++)
        {
            char current = value[index];
            if (current != '\\' || index + 1 >= value.Length)
            {
                output.Append(current);
                continue;
            }
            char escaped = value[++index];
            if (escaped == '"' || escaped == '\\' || escaped == '/')
            {
                output.Append(escaped);
            }
            else if (escaped == 'b')
            {
                output.Append('\b');
            }
            else if (escaped == 'f')
            {
                output.Append('\f');
            }
            else if (escaped == 'n')
            {
                output.Append('\n');
            }
            else if (escaped == 'r')
            {
                output.Append('\r');
            }
            else if (escaped == 't')
            {
                output.Append('\t');
            }
            else if (escaped == 'u' && index + 4 < value.Length)
            {
                string hex = value.Substring(index + 1, 4);
                int code;
                if (int.TryParse(
                    hex,
                    NumberStyles.HexNumber,
                    CultureInfo.InvariantCulture,
                    out code
                ))
                {
                    output.Append((char)code);
                    index += 4;
                }
            }
            else
            {
                output.Append(escaped);
            }
        }
        return output.ToString();
    }

    private static string JsonEscape(string value)
    {
        if (value == null)
        {
            return "";
        }
        StringBuilder output = new StringBuilder();
        foreach (char current in value)
        {
            switch (current)
            {
                case '"':
                    output.Append("\\\"");
                    break;
                case '\\':
                    output.Append("\\\\");
                    break;
                case '\n':
                    output.Append("\\n");
                    break;
                case '\r':
                    output.Append("\\r");
                    break;
                case '\t':
                    output.Append("\\t");
                    break;
                default:
                    if (current < 32)
                    {
                        output.Append("\\u");
                        output.Append(((int)current).ToString("x4"));
                    }
                    else
                    {
                        output.Append(current);
                    }
                    break;
            }
        }
        return output.ToString();
    }

    private static bool IsUnder(string path, string parent)
    {
        try
        {
            string fullPath = Path.GetFullPath(path).TrimEnd(
                Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar
            );
            string fullParent = Path.GetFullPath(parent).TrimEnd(
                Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar
            );
            return fullPath.StartsWith(
                fullParent + Path.DirectorySeparatorChar,
                StringComparison.OrdinalIgnoreCase
            );
        }
        catch
        {
            return false;
        }
    }

    private static void PruneOldVersions(
        string installRoot,
        string currentDir,
        string previousDir
    )
    {
        string versionsDir = Path.Combine(installRoot, "versions");
        try
        {
            if (!Directory.Exists(versionsDir))
            {
                return;
            }

            string current = Path.GetFullPath(currentDir);
            string previous = Path.GetFullPath(previousDir);
            foreach (string directory in Directory.GetDirectories(versionsDir))
            {
                string candidate = Path.GetFullPath(directory);
                bool keepCurrent = string.Equals(
                    candidate,
                    current,
                    StringComparison.OrdinalIgnoreCase
                );
                bool keepPrevious = IsUnder(previous, versionsDir)
                    && string.Equals(
                        candidate,
                        previous,
                        StringComparison.OrdinalIgnoreCase
                    );
                if (!keepCurrent && !keepPrevious && IsUnder(candidate, versionsDir))
                {
                    try
                    {
                        Directory.Delete(candidate, true);
                    }
                    catch (Exception ex)
                    {
                        Console.WriteLine(
                            "Could not remove old version "
                                + candidate
                                + ": "
                                + ex.Message
                        );
                    }
                }
            }

            bool hasVersionRollbackPair = IsUnder(current, versionsDir)
                && IsUnder(previous, versionsDir)
                && !string.Equals(
                    current,
                    previous,
                    StringComparison.OrdinalIgnoreCase
                )
                && HasPortablePayload(current)
                && HasPortablePayload(previous);
            if (hasVersionRollbackPair)
            {
                DeleteLegacyPayload(Path.Combine(installRoot, "app"));
                DeleteLegacyPayload(Path.Combine(installRoot, "python"));
            }
        }
        catch (Exception ex)
        {
            Console.WriteLine("Could not prune old versions: " + ex.Message);
        }
    }

    private static void DeleteLegacyPayload(string directory)
    {
        try
        {
            if (Directory.Exists(directory))
            {
                Directory.Delete(directory, true);
                Console.WriteLine("Removed legacy portable payload " + directory);
            }
        }
        catch (Exception ex)
        {
            Console.WriteLine(
                "Could not remove legacy portable payload "
                    + directory
                    + ": "
                    + ex.Message
            );
        }
    }

    private static string RelativeVersionDirectory(
        string installRoot,
        string candidateDir
    )
    {
        string root = Path.GetFullPath(installRoot).TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar
        );
        string candidate = Path.GetFullPath(candidateDir);
        return candidate.Substring(root.Length).TrimStart(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar
        );
    }

    private static void OpenBrowser(string url)
    {
        try
        {
            ProcessStartInfo info = new ProcessStartInfo();
            info.FileName = url;
            info.UseShellExecute = true;
            Process.Start(info);
        }
        catch
        {
            Console.WriteLine("Open this address in your browser:");
            Console.WriteLine(url);
        }
    }

    private static void StopServer()
    {
        // 主动停止（退出、更新切换、回滚）不算异常退出，不产生 crash 记录。
        suppressCrashReport = true;
        StopProcess(serverProcess);
    }

    private static void StopProcess(Process process)
    {
        try
        {
            if (process != null && !process.HasExited)
            {
                process.Kill();
                process.WaitForExit(3000);
            }
        }
        catch
        {
        }
    }

    private static void TryDelete(string path)
    {
        try
        {
            if (File.Exists(path))
            {
                File.Delete(path);
            }
        }
        catch
        {
        }
    }

    private static string ResolvePort(string configPath)
    {
        string envPort = Environment.GetEnvironmentVariable("TRPG_WEB_PORT");
        if (IsPort(envPort))
        {
            return envPort;
        }
        try
        {
            if (File.Exists(configPath))
            {
                string text = File.ReadAllText(configPath);
                Match match = Regex.Match(text, "\"web_port\"\\s*:\\s*(\\d+)");
                if (match.Success && IsPort(match.Groups[1].Value))
                {
                    return match.Groups[1].Value;
                }
            }
        }
        catch
        {
        }
        return DefaultPort;
    }

    private static bool IsPort(string value)
    {
        int port;
        return !string.IsNullOrWhiteSpace(value)
            && int.TryParse(value, out port)
            && port >= 1
            && port <= 65535;
    }

    private static string Quote(string value)
    {
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }

    private static int Fail(string message)
    {
        Console.WriteLine(message);
        Console.WriteLine();
        // 重定向（CI / 自动化 / 被其它程序拉起）时不能等待按键：Console.ReadKey
        // 在没有真实控制台时会抛 InvalidOperationException，把可读的失败变成崩溃。
        if (!Console.IsInputRedirected && !Console.IsOutputRedirected)
        {
            Console.WriteLine("Press any key to close.");
            Console.ReadKey(true);
        }
        return 1;
    }

    // ---- 崩溃取证（Windows Portable） ------------------------------------
    // 目标：python.exe native crash 时自动留下 MiniDump 与异常退出 metadata，
    // 用户只需把 logs 文件夹打包反馈。这里不尝试诊断崩溃原因本身。

    private sealed class CrashDumpRegistryValue
    {
        public bool Existed;
        public object Data;
        public RegistryValueKind Kind;
    }

    private sealed class CrashDumpRegistrySnapshot
    {
        public bool KeyExisted;
        public CrashDumpRegistryValue DumpFolder;
        public CrashDumpRegistryValue DumpType;
        public CrashDumpRegistryValue DumpCount;
    }

    private static CrashDumpRegistryValue CaptureRegistryValue(RegistryKey key, string name)
    {
        CrashDumpRegistryValue captured = new CrashDumpRegistryValue();
        object data = key.GetValue(name, null, RegistryValueOptions.DoNotExpandEnvironmentNames);
        if (data == null)
        {
            // 值不存在时 GetValueKind 会抛异常，因此只在实际存在时读取。
            captured.Existed = false;
            captured.Kind = RegistryValueKind.String;
            return captured;
        }
        captured.Existed = true;
        captured.Data = data;
        captured.Kind = key.GetValueKind(name);
        return captured;
    }

    /// <summary>
    /// 临时启用 WER LocalDumps（MiniDump，最多 3 个）并返回原值快照。
    /// 任何失败都只打印提示并返回 null——绝不阻止 DiceFrame 启动。
    /// </summary>
    private static CrashDumpRegistrySnapshot EnableCrashDumpCapture(string logsDir)
    {
        // 快照必须在 try 之外持有：三个 value 写到一半失败时，catch 里要立刻把
        // 已经写进去的部分恢复掉，否则会永久污染用户的 LocalDumps 配置。
        CrashDumpRegistrySnapshot snapshot = null;
        try
        {
            string dumpDir = Path.Combine(logsDir, "crash-dumps");
            Directory.CreateDirectory(dumpDir);
            PruneCrashDumps(dumpDir, MaxCrashDumps);

            snapshot = new CrashDumpRegistrySnapshot();
            using (RegistryKey probe = Registry.CurrentUser.OpenSubKey(LocalDumpsPythonKey, false))
            {
                snapshot.KeyExisted = probe != null;
            }
            using (RegistryKey key = Registry.CurrentUser.CreateSubKey(LocalDumpsPythonKey))
            {
                if (key == null)
                {
                    throw new InvalidOperationException("cannot open LocalDumps key");
                }
                // 只快照本功能会改动的三个值，不动用户 key 里的其它配置。
                snapshot.DumpFolder = CaptureRegistryValue(key, "DumpFolder");
                snapshot.DumpType = CaptureRegistryValue(key, "DumpType");
                snapshot.DumpCount = CaptureRegistryValue(key, "DumpCount");

                key.SetValue("DumpFolder", dumpDir, RegistryValueKind.ExpandString);
                // DumpType = 1：MiniDump（本轮不做 Full Dump）。
                key.SetValue("DumpType", 1, RegistryValueKind.DWord);
                key.SetValue("DumpCount", MaxCrashDumps, RegistryValueKind.DWord);
            }
            return snapshot;
        }
        catch (Exception ex)
        {
            // 部分写入失败也要回滚到原值（RestoreCrashDumpCapture 支持只写了一部分）。
            RestoreCrashDumpCapture(snapshot);
            Console.WriteLine("Crash diagnostics could not be enabled: " + ex.Message);
            return null;
        }
    }

    /// <summary>恢复用户原有 WER 配置；失败只告警，不影响退出。</summary>
    private static void RestoreCrashDumpCapture(CrashDumpRegistrySnapshot snapshot)
    {
        if (snapshot == null)
        {
            return;
        }
        try
        {
            bool removeEmptyKey = false;
            using (RegistryKey key = Registry.CurrentUser.OpenSubKey(LocalDumpsPythonKey, true))
            {
                if (key == null)
                {
                    return;
                }
                RestoreRegistryValue(key, "DumpFolder", snapshot.DumpFolder);
                RestoreRegistryValue(key, "DumpType", snapshot.DumpType);
                RestoreRegistryValue(key, "DumpCount", snapshot.DumpCount);
                removeEmptyKey = !snapshot.KeyExisted
                    && key.ValueCount == 0
                    && key.SubKeyCount == 0;
                if (removeEmptyKey)
                {
                    // 删除前必须先关闭句柄，否则 DeleteSubKey 会失败。
                    key.Close();
                }
            }
            if (removeEmptyKey)
            {
                Registry.CurrentUser.DeleteSubKey(LocalDumpsPythonKey, false);
            }
        }
        catch (Exception ex)
        {
            Console.WriteLine("Crash diagnostics registry cleanup failed: " + ex.Message);
        }
    }

    private static void RestoreRegistryValue(
        RegistryKey key,
        string name,
        CrashDumpRegistryValue original
    )
    {
        if (original != null && original.Existed && original.Data != null)
        {
            key.SetValue(name, original.Data, original.Kind);
            return;
        }
        // 原本没有该值：只删掉本次临时写入的，不影响其它配置。
        if (key.GetValue(name) != null)
        {
            key.DeleteValue(name, false);
        }
    }

    /// <summary>只保留最近若干个 dump，避免长期占用磁盘。</summary>
    private static void PruneCrashDumps(string dumpDir, int keep)
    {
        try
        {
            FileInfo[] dumps = new DirectoryInfo(dumpDir).GetFiles("*.dmp");
            if (dumps.Length <= keep)
            {
                return;
            }
            Array.Sort(dumps, delegate(FileInfo left, FileInfo right)
            {
                return right.LastWriteTimeUtc.CompareTo(left.LastWriteTimeUtc);
            });
            for (int index = keep; index < dumps.Length; index++)
            {
                TryDelete(dumps[index].FullName);
            }
        }
        catch
        {
        }
    }

    /// <summary>
    /// 找本次崩溃对应的 dump。WER 自己决定文件名（常见 python.exe.&lt;pid&gt;.dmp），
    /// 这里只接受文件名包含当前 server pid、且晚于启动时间的那个：同机另一个
    /// python.exe 恰好崩溃时，不能把别人的 dump 当成本次崩溃的证据。
    /// 找不到就返回 null（metadata 照常写）。
    /// </summary>
    private static string FindLatestCrashDump(string dumpDir, DateTime sinceUtc)
    {
        try
        {
            if (!Directory.Exists(dumpDir) || serverPid <= 0)
            {
                return null;
            }
            string pidToken = "." + serverPid.ToString(CultureInfo.InvariantCulture) + ".";
            FileInfo newest = null;
            foreach (FileInfo file in new DirectoryInfo(dumpDir).GetFiles("*.dmp"))
            {
                if (file.Name.IndexOf(pidToken, StringComparison.OrdinalIgnoreCase) < 0)
                {
                    continue;
                }
                // 允许少量时钟/文件时间粒度误差，避免漏掉刚写出的 dump。
                if (file.LastWriteTimeUtc.AddSeconds(2) < sinceUtc)
                {
                    continue;
                }
                if (newest == null || file.LastWriteTimeUtc > newest.LastWriteTimeUtc)
                {
                    newest = file;
                }
            }
            return newest == null ? null : newest.FullName;
        }
        catch
        {
            return null;
        }
    }

    private static string WaitForCrashDump(string dumpDir, DateTime sinceUtc, TimeSpan timeout)
    {
        DateTime deadline = DateTime.UtcNow.Add(timeout);
        while (true)
        {
            string found = FindLatestCrashDump(dumpDir, sinceUtc);
            if (found != null || DateTime.UtcNow >= deadline)
            {
                return found;
            }
            Thread.Sleep(500);
        }
    }

    private static string FormatExitCode(int exitCode)
    {
        return "0x" + unchecked((uint)exitCode).ToString("X8", CultureInfo.InvariantCulture);
    }

    /// <summary>仅用于展示的粗分类；不据此自动下结论。</summary>
    private static string ClassifyExitCode(uint code)
    {
        switch (code)
        {
            case 0xC0000005:
                return "access_violation";
            case 0xC00000FD:
                return "stack_overflow";
            case 0xC000001D:
                return "illegal_instruction";
            case 0xC0000374:
                return "heap_corruption";
            case 0xC0000409:
                return "fail_fast";
            default:
                return "unexpected_exit";
        }
    }

    /// <summary>
    /// 记录一次异常退出并给出反馈路径。只写诊断所需的最小信息：
    /// 不含 API key / token / prompt / 聊天正文 / 角色信息 / 环境变量。
    /// </summary>
    private static void HandleUnexpectedServerExit(
        string logsDir,
        Process process,
        string activeDir
    )
    {
        int exitCode;
        try
        {
            exitCode = process.ExitCode;
        }
        catch
        {
            exitCode = 1;
        }
        DateTime crashedAtUtc = DateTime.UtcNow;
        uint code = unchecked((uint)exitCode);
        string formatted = FormatExitCode(exitCode);
        string classification = ClassifyExitCode(code);
        DateTime dumpSinceUtc = serverStartedAtUtc == DateTime.MinValue
            ? crashedAtUtc
            : serverStartedAtUtc;
        // WER 由 WerFault 异步写盘：给一个有限窗口再判定，既能让 dump 落到我们的
        // 目录（临时配置此时仍然有效），也不会让退出流程无限等待。
        string dumpFile = WaitForCrashDump(
            Path.Combine(logsDir, "crash-dumps"),
            dumpSinceUtc,
            TimeSpan.FromSeconds(CrashDumpWaitSeconds)
        );

        try
        {
            WriteCrashRecord(
                logsDir,
                exitCode: formatted,
                classification: classification,
                crashedAtUtc: crashedAtUtc,
                dumpFile: dumpFile,
                activeDir: activeDir
            );
        }
        catch (Exception ex)
        {
            Console.WriteLine("Crash diagnostics could not be written: " + ex.Message);
        }

        Console.WriteLine();
        Console.WriteLine("DiceFrame 后端异常退出。");
        Console.WriteLine();
        Console.WriteLine("错误代码：" + formatted);
        Console.WriteLine("类型：" + classification);
        Console.WriteLine();
        if (dumpFile != null)
        {
            Console.WriteLine("崩溃诊断文件已保存到：");
            Console.WriteLine(Path.GetDirectoryName(dumpFile));
            Console.WriteLine();
        }
        Console.WriteLine("反馈问题时请将 logs 文件夹打包发送。");
        WaitForExitAcknowledgement();
    }

    private static void WriteCrashRecord(
        string logsDir,
        string exitCode,
        string classification,
        DateTime crashedAtUtc,
        string dumpFile,
        string activeDir
    )
    {
        DateTime startedUtc = serverStartedAtUtc == DateTime.MinValue ? crashedAtUtc : serverStartedAtUtc;
        double uptimeSeconds = Math.Max(0d, (crashedAtUtc - startedUtc).TotalSeconds);
        string dumpRelative = "";
        if (dumpFile != null)
        {
            string dumpDir = Path.Combine(logsDir, "crash-dumps");
            dumpRelative = IsUnder(dumpFile, dumpDir)
                ? "crash-dumps/" + Path.GetFileName(dumpFile)
                : Path.GetFileName(dumpFile);
        }
        StringBuilder json = new StringBuilder();
        json.Append("{\n");
        json.Append("  \"schema\": 1,\n");
        json.Append("  \"process\": \"python.exe\",\n");
        json.Append("  \"pid\": " + serverPid.ToString(CultureInfo.InvariantCulture) + ",\n");
        json.Append("  \"exit_code\": \"" + JsonEscape(exitCode) + "\",\n");
        json.Append("  \"classification\": \"" + JsonEscape(classification) + "\",\n");
        json.Append(
            "  \"started_at_utc\": \"" +
            startedUtc.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ", CultureInfo.InvariantCulture) +
            "\",\n"
        );
        json.Append(
            "  \"crashed_at_utc\": \"" +
            crashedAtUtc.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ", CultureInfo.InvariantCulture) +
            "\",\n"
        );
        json.Append(
            "  \"uptime_seconds\": " +
            ((long)Math.Round(uptimeSeconds)).ToString(CultureInfo.InvariantCulture) +
            ",\n"
        );
        json.Append("  \"dump\": " + (dumpRelative.Length == 0 ? "null" : "\"" + JsonEscape(dumpRelative) + "\"") + ",\n");
        string versionDir = string.IsNullOrEmpty(activeDir) ? "" : activeDir;
        json.Append("  \"active_version_dir\": \"" + JsonEscape(versionDir) + "\"\n");
        json.Append("}\n");
        AtomicWriteText(Path.Combine(logsDir, "last-crash.json"), json.ToString());
    }

    /// <summary>交互式运行时让用户看到反馈路径；重定向（CI/自动化）时不阻塞。</summary>
    private static void WaitForExitAcknowledgement()
    {
        try
        {
            if (Console.IsInputRedirected || Console.IsOutputRedirected)
            {
                return;
            }
            Console.WriteLine();
            Console.WriteLine("按 Enter 键退出……");
            Console.ReadLine();
        }
        catch
        {
        }
    }
}
