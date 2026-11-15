using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Threading;

// 启动器与后端的关系（谁的后端、能不能停、退出算不算崩溃），与服务端
// src/webui/services/launcher_link.py 是同一份契约。
//
// - 归属：端口上的服务只有通过实例握手才算本安装的后端。启动器在 data/_launcher/
//   instance.json 维护一把随机密钥（换了数据目录位置就重新生成），后端对启动器给的
//   nonce 做 HMAC，同时认证自己的 boot_id 与 pid。另一份安装、旧版本后端、任何伪造
//   /api/config 的本地服务都答不出来：一律按端口冲突报告，不接管、不打开它的页面，
//   更不会把本安装的首次访问密码注入过去。
// - 生命周期：Owned = 本启动器启动的后端（含它 execv 自重启后的接班进程），关窗口时停止；
//   Attached = 启动时发现、经握手确认属于本安装的后端，关窗口不停，更新切换前只走受鉴权
//   的优雅停止，停不下来就拒绝本次更新，绝不按端口去杀进程。
// - 自重启：Windows 上 os.execv 会换 PID、原进程以 0 退出。只有「退出码 0 + 与这个进程
//   匹配的重启意图」才在限定时间内等新的 boot_id 通过握手后重新接管；非 0 退出、没有
//   意图的退出都按原样处理，不会吞掉真正的崩溃。
internal static partial class DiceFrameLauncher
{
    private enum ServerOwnership
    {
        None,
        Owned,
        Attached,
    }

    private sealed class ServerIdentity
    {
        public string Url;
        public int Pid;
        public string BootId;
        public string Version;
    }

    private const string InstanceFileName = "instance.json";
    private const string RestartIntentFileName = "restart-intent.json";
    private const string IdentityPath = "/api/system/launcher/identity";
    private const string StopPath = "/api/system/launcher/stop";
    private const string HealthPath = "/api/system/update/health";
    private const string IdentityLabel = "diceframe-launcher-identity/v1";
    private const string StopLabel = "diceframe-launcher-stop/v1";
    // 宣告过的自重启（切换 HTTPS 会先生成证书）最多等这么久；与首次启动同一上限。
    private const int RestartFollowSeconds = ReadyTimeoutSeconds;
    // 接管的实例退出时我们不知道原因（例如另一个启动器正在更新它）：短暂等一下再放弃。
    private const int AttachedFollowSeconds = 15;
    private const int AttachedStopTimeoutSeconds = 20;
    private const int PortReleaseTimeoutSeconds = 10;

    private static ServerOwnership serverOwnership = ServerOwnership.None;
    // 当前后端经验证的身份；为 null 表示还没确认它在服务（界面不能打开）。
    private static ServerIdentity currentIdentity;
    private static string serverBootId = "";

    // ---- 实例密钥 ----

    private static string LauncherStateDir()
    {
        return Path.Combine(launcherDataDir, "_launcher");
    }

    private static void EnsureInstanceKey(string dataDir)
    {
        try
        {
            string path = Path.Combine(LauncherStateDir(), InstanceFileName);
            string expectedDir = Path.GetFullPath(dataDir);
            if (File.Exists(path))
            {
                string json = File.ReadAllText(path, Encoding.UTF8);
                // 整个文件夹被复制成另一份安装时，密钥也跟着复制了：按数据目录位置识别，
                // 位置变了就换一把，两份安装从此互不承认。
                if (
                    IsInstanceKey(JsonString(json, "key"))
                    && string.Equals(
                        JsonString(json, "data_dir"),
                        expectedDir,
                        StringComparison.OrdinalIgnoreCase
                    )
                )
                {
                    return;
                }
            }
            AtomicWriteText(
                path,
                "{\n"
                    + "  \"schema\": 1,\n"
                    + "  \"key\": \"" + RandomHex(32) + "\",\n"
                    + "  \"data_dir\": \"" + JsonEscape(expectedDir) + "\"\n"
                    + "}\n"
            );
        }
        catch (Exception ex)
        {
            Console.WriteLine(
                "Instance key unavailable; running backends cannot be verified: " + ex.Message
            );
        }
    }

    // 每次都重新读：同一安装的两个启动器可能先后生成密钥，不能信任启动时的缓存。
    private static byte[] ReadInstanceKey()
    {
        try
        {
            string json = File.ReadAllText(
                Path.Combine(LauncherStateDir(), InstanceFileName),
                Encoding.UTF8
            );
            string key = JsonString(json, "key");
            return IsInstanceKey(key) ? HexToBytes(key) : null;
        }
        catch
        {
            return null;
        }
    }

    private static bool IsInstanceKey(string value)
    {
        if (value == null || value.Length != 64)
        {
            return false;
        }
        foreach (char current in value)
        {
            if (!((current >= '0' && current <= '9') || (current >= 'a' && current <= 'f')))
            {
                return false;
            }
        }
        return true;
    }

    private static string RandomHex(int bytes)
    {
        byte[] buffer = new byte[bytes];
        using (RNGCryptoServiceProvider random = new RNGCryptoServiceProvider())
        {
            random.GetBytes(buffer);
        }
        return BytesToHex(buffer);
    }

    private static string BytesToHex(byte[] data)
    {
        StringBuilder builder = new StringBuilder(data.Length * 2);
        foreach (byte value in data)
        {
            builder.Append(value.ToString("x2", CultureInfo.InvariantCulture));
        }
        return builder.ToString();
    }

    private static byte[] HexToBytes(string hex)
    {
        byte[] output = new byte[hex.Length / 2];
        for (int index = 0; index < output.Length; index++)
        {
            output[index] = byte.Parse(
                hex.Substring(index * 2, 2),
                NumberStyles.HexNumber,
                CultureInfo.InvariantCulture
            );
        }
        return output;
    }

    private static string LinkMac(byte[] key, params string[] parts)
    {
        using (HMACSHA256 mac = new HMACSHA256(key))
        {
            return BytesToHex(mac.ComputeHash(Encoding.UTF8.GetBytes(string.Join("\n", parts))));
        }
    }

    private static bool FixedTimeEquals(string left, string right)
    {
        if (left == null || right == null || left.Length != right.Length)
        {
            return false;
        }
        int difference = 0;
        for (int index = 0; index < left.Length; index++)
        {
            difference |= left[index] ^ right[index];
        }
        return difference == 0;
    }

    // ---- 探测 ----

    private static string[] CandidateUrls(string url)
    {
        // 切换连接模式后启动器缓存的 scheme 可能已经过期：两种都试，身份校验完全一样。
        string https = "https://127.0.0.1:" + launcherPort;
        string http = "http://127.0.0.1:" + launcherPort;
        if (string.Equals(url, https, StringComparison.OrdinalIgnoreCase))
        {
            return new string[] { https, http };
        }
        if (string.Equals(url, http, StringComparison.OrdinalIgnoreCase) || string.IsNullOrEmpty(url))
        {
            return new string[] { http, https };
        }
        return new string[] { url, https, http };
    }

    /// <summary>
    /// 请这个地址上的服务证明自己属于本安装；证明不了（没有密钥、不是 DiceFrame、
    /// 别的安装、旧版本没有握手端点）一律返回 null。
    /// </summary>
    private static ServerIdentity ProbeIdentity(string baseUrl)
    {
        byte[] key = ReadInstanceKey();
        if (key == null)
        {
            return null;
        }
        try
        {
            string nonce = RandomHex(16);
            string body = HttpGet(baseUrl + IdentityPath + "?nonce=" + nonce);
            string bootId = JsonString(body, "boot_id");
            int pid = JsonInt(body, "pid", 0);
            if (bootId.Length == 0 || pid <= 0)
            {
                return null;
            }
            string expected = LinkMac(
                key,
                IdentityLabel,
                nonce,
                bootId,
                pid.ToString(CultureInfo.InvariantCulture)
            );
            if (!FixedTimeEquals(expected, JsonString(body, "proof")))
            {
                return null;
            }
            return new ServerIdentity
            {
                Url = baseUrl,
                Pid = pid,
                BootId = bootId,
                Version = JsonString(body, "version"),
            };
        }
        catch
        {
            return null;
        }
    }

    private static ServerIdentity ProbeOwnInstance(string url)
    {
        foreach (string candidate in CandidateUrls(url))
        {
            ServerIdentity identity = ProbeIdentity(candidate);
            if (identity != null)
            {
                return identity;
            }
        }
        return null;
    }

    // 公开健康端点（v1.6.0 起都有 pid / boot_id）：用于本启动器亲自启动的进程——
    // pid 与子进程句柄一致就证明端口上是它，旧版本载荷（回滚目标）也适用。
    private static ServerIdentity ProbeHealth(string baseUrl)
    {
        try
        {
            string body = HttpGet(baseUrl + HealthPath);
            int pid = JsonInt(body, "pid", 0);
            if (pid <= 0)
            {
                return null;
            }
            return new ServerIdentity
            {
                Url = baseUrl,
                Pid = pid,
                BootId = JsonString(body, "boot_id"),
                Version = JsonString(body, "version"),
            };
        }
        catch
        {
            return null;
        }
    }

    private static ServerIdentity ProbeOwnedServer(Process process, string url)
    {
        if (process == null)
        {
            return null;
        }
        foreach (string candidate in CandidateUrls(url))
        {
            ServerIdentity health = ProbeHealth(candidate);
            if (health != null && health.Pid == process.Id)
            {
                return health;
            }
        }
        return null;
    }

    private static ServerIdentity WaitForOwnedServer(Process process, string url, TimeSpan timeout)
    {
        DateTime deadline = DateTime.UtcNow.Add(timeout);
        while (DateTime.UtcNow < deadline && !shuttingDown)
        {
            if (process == null || process.HasExited)
            {
                return null;
            }
            ServerIdentity ready = ProbeOwnedServer(process, url);
            if (ready != null)
            {
                return ready;
            }
            Thread.Sleep(500);
        }
        return null;
    }

    private static bool IsPortListening()
    {
        int port;
        if (!int.TryParse(launcherPort, out port))
        {
            return false;
        }
        try
        {
            using (TcpClient client = new TcpClient())
            {
                IAsyncResult attempt = client.BeginConnect(IPAddress.Loopback, port, null, null);
                if (!attempt.AsyncWaitHandle.WaitOne(TimeSpan.FromSeconds(1)))
                {
                    return false;
                }
                client.EndConnect(attempt);
                return client.Connected;
            }
        }
        catch
        {
            return false;
        }
    }

    private static bool WaitForPortRelease(TimeSpan timeout)
    {
        DateTime deadline = DateTime.UtcNow.Add(timeout);
        while (IsPortListening())
        {
            if (DateTime.UtcNow >= deadline)
            {
                return false;
            }
            Thread.Sleep(500);
        }
        return true;
    }

    /// <summary>
    /// 启动前检查端口：返回 true 表示被无法证明归属的服务占着（端口冲突）；
    /// own 非空表示端口上是本安装的后端，可以接管。
    /// </summary>
    private static bool PortHeldByUnverifiedServer(string url, out ServerIdentity own)
    {
        own = ProbeOwnInstance(url);
        return own == null && IsPortListening();
    }

    private static string PortConflictMessage()
    {
        return "端口 " + launcherPort + " 已被占用，而且占用者无法证明自己是这份 DiceFrame 的后端，"
            + "所以没有接管它。\n\n"
            + "它可能是另一份 DiceFrame、旧版本留在后台的进程，或其它程序。请先关闭它"
            + "（必要时在任务管理器里结束对应的 python.exe）再启动；也可以在 data/config.json "
            + "里修改 web_port。\n\n"
            + "Port " + launcherPort + " is in use by a server that is not this DiceFrame installation.";
    }

    // ---- 绑定 ----

    // 经握手认证过的 pid 才会走到这里；立刻取句柄，之后 Kill/等待都作用在这个句柄上。
    // 句柄打开之后再握手一次：同一个 boot_id 仍在应答，说明句柄指向的就是它，而不是证明
    // 之后才复用了这个 PID 的别的进程。取不到（例如对方以管理员身份运行）就不监控退出。
    private static Process OpenVerifiedProcess(ServerIdentity identity)
    {
        Process process = null;
        try
        {
            process = Process.GetProcessById(identity.Pid);
            if (process.Handle == IntPtr.Zero)
            {
                process.Dispose();
                return null;
            }
            ServerIdentity again = ProbeIdentity(identity.Url);
            if (again == null || again.Pid != identity.Pid || again.BootId != identity.BootId)
            {
                process.Dispose();
                return null;
            }
            return process;
        }
        catch
        {
            if (process != null)
            {
                process.Dispose();
            }
            return null;
        }
    }

    private static void BindStartedServer(Process process)
    {
        serverProcess = process;
        serverOwnership = ServerOwnership.Owned;
        currentIdentity = null;
        serverBootId = "";
    }

    private static void BindIdentity(ServerIdentity identity)
    {
        currentIdentity = identity;
        serverBootId = identity == null ? "" : identity.BootId ?? "";
    }

    private static void BindVerifiedProcess(ServerIdentity identity, ServerOwnership ownership)
    {
        serverOwnership = ownership;
        serverProcess = OpenVerifiedProcess(identity);
        serverPid = identity.Pid;
        serverStartedAtUtc = DateTime.UtcNow;
        suppressCrashReport = false;
        BindIdentity(identity);
        if (serverProcess == null)
        {
            Console.WriteLine("Could not open the backend process; its exit will not be monitored.");
        }
    }

    private static void ClearServerBinding()
    {
        serverProcess = null;
        serverOwnership = ServerOwnership.None;
        currentIdentity = null;
        serverBootId = "";
    }

    private static bool ServerHasExited()
    {
        try
        {
            return serverProcess != null && serverProcess.HasExited;
        }
        catch
        {
            return false;
        }
    }

    private static int SafeExitCode(Process process)
    {
        try
        {
            return process.ExitCode;
        }
        catch
        {
            return -1;
        }
    }

    // ---- 停止（更新切换） ----

    private static bool RequestGracefulStop(ServerIdentity identity)
    {
        byte[] key = ReadInstanceKey();
        if (key == null || identity == null || string.IsNullOrEmpty(identity.BootId))
        {
            return false;
        }
        try
        {
            string nonce = RandomHex(16);
            string json = "{\"nonce\": \"" + nonce + "\", \"boot_id\": \""
                + JsonEscape(identity.BootId) + "\", \"proof\": \""
                + LinkMac(key, StopLabel, nonce, identity.BootId) + "\"}";
            using (StringContent content = new StringContent(json, Encoding.UTF8, "application/json"))
            using (
                HttpResponseMessage response = localHttpClient
                    .PostAsync(identity.Url + StopPath, content)
                    .GetAwaiter()
                    .GetResult()
            )
            {
                int status = (int)response.StatusCode;
                return status >= 200 && status < 300;
            }
        }
        catch
        {
            return false;
        }
    }

    private static bool WaitForAttachedExit(ServerIdentity identity, TimeSpan timeout)
    {
        DateTime deadline = DateTime.UtcNow.Add(timeout);
        while (DateTime.UtcNow < deadline)
        {
            bool gone = serverProcess != null
                ? ServerHasExited()
                : ProbeIdentity(identity.Url) == null;
            if (gone)
            {
                return true;
            }
            Thread.Sleep(500);
        }
        return false;
    }

    /// <summary>
    /// 更新切换前停掉当前后端并确认端口已释放。返回 null 表示可以切换；否则返回原因。
    /// stillRunning 表示旧后端仍在服务——此时调用方必须放弃本次更新，不能去抢端口。
    /// </summary>
    private static string StopServerForSwitch(out bool stillRunning)
    {
        stillRunning = false;
        if (serverOwnership == ServerOwnership.Owned)
        {
            StopProcess(serverProcess);
            if (ServerHasExited() || serverProcess == null)
            {
                ClearServerBinding();
            }
            else
            {
                stillRunning = true;
                return "旧版本进程没有退出";
            }
        }
        else if (serverOwnership == ServerOwnership.Attached)
        {
            // 不是本启动器启动的实例：只能请它自己优雅退出，停不下来就放弃本次更新。
            ServerIdentity attached = currentIdentity;
            if (!RequestGracefulStop(attached))
            {
                stillRunning = true;
                return "正在运行的 DiceFrame 没有接受停止请求";
            }
            if (!WaitForAttachedExit(attached, TimeSpan.FromSeconds(AttachedStopTimeoutSeconds)))
            {
                stillRunning = true;
                return "正在运行的 DiceFrame 在 " + AttachedStopTimeoutSeconds + " 秒内没有退出";
            }
            ClearServerBinding();
        }
        if (!WaitForPortRelease(TimeSpan.FromSeconds(PortReleaseTimeoutSeconds)))
        {
            return "端口 " + launcherPort + " 仍被占用";
        }
        return null;
    }

    // ---- 自重启跟随 ----

    private static bool ConsumeRestartIntent(int pid, string bootId)
    {
        string path = Path.Combine(LauncherStateDir(), RestartIntentFileName);
        try
        {
            if (pid <= 0 || !File.Exists(path))
            {
                return false;
            }
            string json = File.ReadAllText(path, Encoding.UTF8);
            bool matches = JsonInt(json, "pid", 0) == pid
                && (string.IsNullOrEmpty(bootId) || JsonString(json, "boot_id") == bootId);
            if (matches)
            {
                TryDelete(path);
            }
            return matches;
        }
        catch
        {
            return false;
        }
    }

    private static ServerIdentity WaitForSuccessor(string url, string previousBootId, TimeSpan timeout)
    {
        DateTime deadline = DateTime.UtcNow.Add(timeout);
        while (DateTime.UtcNow < deadline && !shuttingDown)
        {
            ServerIdentity successor = ProbeOwnInstance(url);
            if (successor != null && successor.BootId != previousBootId)
            {
                return successor;
            }
            Thread.Sleep(500);
        }
        return null;
    }

    /// <summary>
    /// 当前后端进程已退出：判断是不是自重启并尝试接上接班进程。
    /// 返回 true 表示已重新接管（界面继续）；announced 表示退出前宣告过重启。
    /// </summary>
    private static bool FollowServerRestart(string url, out bool announced)
    {
        int exitCode = serverProcess == null ? -1 : SafeExitCode(serverProcess);
        announced = exitCode == 0 && ConsumeRestartIntent(serverPid, serverBootId);
        bool attached = serverOwnership == ServerOwnership.Attached;
        if (!announced && !attached)
        {
            return false;
        }
        Console.WriteLine(
            announced
                ? "DiceFrame is restarting; waiting for the new backend to prove it belongs here..."
                : "The attached DiceFrame stopped; checking whether it comes back..."
        );
        ServerIdentity successor = WaitForSuccessor(
            url,
            serverBootId,
            TimeSpan.FromSeconds(announced ? RestartFollowSeconds : AttachedFollowSeconds)
        );
        if (successor == null)
        {
            return false;
        }
        BindVerifiedProcess(successor, serverOwnership);
        Console.WriteLine(
            "DiceFrame restarted; reattached to " + successor.Url + " (pid "
                + successor.Pid.ToString(CultureInfo.InvariantCulture) + ")."
        );
        return true;
    }

    /// <summary>后端退出且没有接班进程：按归属给出结果。返回启动器退出码。</summary>
    private static int ReportServerExit(string logsDir, string activeDir, bool announced)
    {
        Process exited = serverProcess;
        int exitCode = exited == null ? 1 : SafeExitCode(exited);
        if (announced)
        {
            return Fail(
                "DiceFrame 重启后没有在 " + RestartFollowSeconds + " 秒内恢复服务。\n\n"
                    + "反馈问题时请将 logs 文件夹打包发送：\n" + logsDir
            );
        }
        if (serverOwnership == ServerOwnership.Attached && exitCode == 0)
        {
            // 接管的实例正常退出（例如在别处被关掉）：不是本启动器的崩溃。
            Console.WriteLine("The attached DiceFrame has stopped.");
            if (consoleDetached)
            {
                NotifyUser("DiceFrame 已停止运行。");
            }
            return 0;
        }
        if (exited != null && !suppressCrashReport)
        {
            HandleUnexpectedServerExit(logsDir, exited, activeDir);
        }
        return exited == null ? 1 : exitCode;
    }
}
