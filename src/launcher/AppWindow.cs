using System;
using System.Drawing;
using System.IO;
using System.Net.Security;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

// 应用窗口：用 WebView2（系统自带的 Edge 内核）把 DiceFrame 开在自己的窗口里，而不是默认
// 浏览器的一个标签页，并且只留这一个窗口。
//
// - 启动时先开窗口显示「正在启动」，服务就绪后再加载页面；窗口一开，启动器就与控制台
//   脱离（FreeConsole），控制台窗口随之关闭——Windows Terminal 作为默认终端时也一样，
//   单纯隐藏窗口做不到。之后启动器的输出写进 logs/launcher.log，python.exe 也不再弹窗口；
//   出错时用对话框告诉用户，而不是在已经看不见的控制台里等按键。
// - 首次启动的访问密码只在 data/access_token.txt 里（用户改过密码后服务端会删掉它）：
//   文件存在时，在页面脚本运行前把它写进前端保存登录凭据的同一个 localStorage 键，
//   第一次打开就直接进入；服务端启动时会删除与当前密码不一致的旧文件，所以它总是有效的。
// - WebView2 SDK 程序集随版本载荷分发在 app/launcher/webview2/：旧版更新器、新版更新器和
//   覆盖升级都整体搬运 app/，启动器自己只是一个 exe。用之前复制到 data/_launcher/webview2/
//   再加载，免得进程锁住 versions/ 里的文件、让之后的版本清理删不掉。
// - 任何一步不可用（缺程序集、缺 WebView2 运行时、初始化失败）都保留控制台并退回默认浏览器；
//   启动参数 --browser 强制用浏览器。关掉应用窗口与关掉控制台一样：停止服务并退出。
//   其他玩家仍然用浏览器或移动端经链接加入，这个窗口只是房主本机的入口。
internal static partial class DiceFrameLauncher
{
    private static readonly string[] WebView2Files =
    {
        "Microsoft.Web.WebView2.Core.dll",
        "Microsoft.Web.WebView2.WinForms.dll",
        "WebView2Loader.dll",
    };
    private const int AppWindowStartTimeoutSeconds = 30;
    private const long LauncherLogMaxBytes = 1024 * 1024;
    // 与 frontend-v2/src/api/connection.ts 的 ACCESS_TOKEN_KEY 一致（内置前端没有独立后端地址）。
    private const string FrontendAccessTokenKey = "trpg_access_token";
    private const string SplashHtml =
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>DiceFrame</title>"
        + "<style>html,body{height:100%;margin:0;background:#0d1520;color:#d8c9a3;"
        + "font-family:'Segoe UI','Microsoft YaHei',sans-serif;display:flex;align-items:center;"
        + "justify-content:center;flex-direction:column}h1{font-weight:600;letter-spacing:.08em;"
        + "margin:0 0 12px}p{margin:4px;color:#8fa1b5}</style></head><body>"
        + "<h1>DiceFrame</h1><p>正在启动…</p><p>Starting…</p></body></html>";

    [DllImport("kernel32.dll")]
    private static extern bool FreeConsole();

    private static bool preferBrowser;
    private static bool consoleDetached;
    private static string launcherLogsDir = "";
    private static string webView2Dir = "";
    private static bool webView2ResolverInstalled;
    // 由窗口线程设置：在窗口线程上导航到给定地址。为 null 表示应用窗口没有打开。
    private static Action<string> appWindowNavigate;

    // 打开界面：应用窗口已开就加载页面；还没开就试着开；都不行就用默认浏览器。
    // 只接受已验证的目标——本启动器亲自启动的后端（健康检查 pid 与子进程一致），或经实例
    // 握手证明属于本安装的后端：首次访问密码会随页面注入，绝不能送给端口上的别的服务。
    private static void OpenUi(ServerIdentity verified, string payloadDir)
    {
        if (verified == null || string.IsNullOrEmpty(verified.Url))
        {
            Console.WriteLine("Refusing to open an unverified backend address.");
            return;
        }
        string url = verified.Url;
        if (appWindowNavigate == null && !preferBrowser && StartAppWindow(payloadDir))
        {
            DetachConsole(launcherLogsDir);
        }
        Action<string> navigate = appWindowNavigate;
        if (navigate != null)
        {
            navigate(url);
            return;
        }
        OpenBrowser(url);
    }

    private static bool StartAppWindow(string payloadDir)
    {
        if (string.IsNullOrEmpty(payloadDir) || !StageWebView2(payloadDir))
        {
            return false;
        }
        ManualResetEvent settled = new ManualResetEvent(false);
        Thread thread = new Thread(delegate()
        {
            try
            {
                // RunAppWindow 引用 WebView2 类型：程序集缺失时异常在这里抛出并被接住，
                // 而不是让启动器本身起不来。
                RunAppWindow(delegate { settled.Set(); });
            }
            catch (Exception ex)
            {
                Console.WriteLine("App window unavailable: " + ex.Message);
            }
            finally
            {
                appWindowNavigate = null;
                settled.Set();
            }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.IsBackground = true;
        thread.Start();
        settled.WaitOne(TimeSpan.FromSeconds(AppWindowStartTimeoutSeconds));
        return appWindowNavigate != null;
    }

    // 覆盖升级时，界面用候选版本带的 WebView2 程序集（旧版本可能根本没有）。
    private static string AdoptedCandidateDirectory(string restartSignal)
    {
        try
        {
            return JsonString(File.ReadAllText(restartSignal, Encoding.UTF8), "candidate_dir");
        }
        catch
        {
            return "";
        }
    }

    private static bool StageWebView2(string payloadDir)
    {
        try
        {
            string source = Path.Combine(payloadDir, "app", "launcher", "webview2");
            string target = Path.Combine(launcherDataDir, "_launcher", "webview2");
            foreach (string name in WebView2Files)
            {
                if (!File.Exists(Path.Combine(source, name)))
                {
                    return false;
                }
            }
            Directory.CreateDirectory(target);
            foreach (string name in WebView2Files)
            {
                string from = Path.Combine(source, name);
                string to = Path.Combine(target, name);
                bool same = File.Exists(to)
                    && new FileInfo(to).Length == new FileInfo(from).Length
                    && File.GetLastWriteTimeUtc(to) == File.GetLastWriteTimeUtc(from);
                if (!same)
                {
                    try
                    {
                        File.Copy(from, to, true);
                    }
                    catch (IOException)
                    {
                        // 另一个启动器实例正在用旧副本：有就先用着，下次启动再更新。
                        if (!File.Exists(to))
                        {
                            throw;
                        }
                    }
                }
            }
            webView2Dir = target;
            if (!webView2ResolverInstalled)
            {
                AppDomain.CurrentDomain.AssemblyResolve += ResolveWebView2Assembly;
                webView2ResolverInstalled = true;
            }
            return true;
        }
        catch (Exception ex)
        {
            Console.WriteLine("App window unavailable: " + ex.Message);
            return false;
        }
    }

    private static Assembly ResolveWebView2Assembly(object sender, ResolveEventArgs args)
    {
        string name = new AssemblyName(args.Name).Name;
        if (
            webView2Dir.Length == 0
            || !name.StartsWith("Microsoft.Web.WebView2", StringComparison.OrdinalIgnoreCase)
        )
        {
            return null;
        }
        string path = Path.Combine(webView2Dir, name + ".dll");
        return File.Exists(path) ? Assembly.LoadFrom(path) : null;
    }

    private static void RunAppWindow(Action settled)
    {
        Application.EnableVisualStyles();
        Form form = new Form();
        form.Text = "DiceFrame";
        form.BackColor = Color.FromArgb(13, 21, 32);
        try
        {
            form.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        }
        catch
        {
        }
        Rectangle area = Screen.PrimaryScreen.WorkingArea;
        form.StartPosition = FormStartPosition.CenterScreen;
        form.Size = new Size(
            Math.Min(1366, area.Width * 9 / 10),
            Math.Min(900, area.Height * 9 / 10)
        );
        form.MinimumSize = new Size(800, 560);
        WebView2 view = new WebView2();
        view.Dock = DockStyle.Fill;
        view.DefaultBackgroundColor = form.BackColor;
        form.Controls.Add(view);

        bool ready = false;
        form.Load += async delegate
        {
            try
            {
                CoreWebView2Environment.SetLoaderDllFolderPath(webView2Dir);
                CoreWebView2Environment environment = await CoreWebView2Environment.CreateAsync(
                    null,
                    Path.Combine(launcherDataDir, "_launcher", "webview2-profile"),
                    null
                );
                await view.EnsureCoreWebView2Async(environment);
                CoreWebView2 core = view.CoreWebView2;
                ConfigureAppWindow(form, core);
                core.NavigateToString(SplashHtml);
                ready = true;
                appWindowNavigate = delegate(string url)
                {
                    try
                    {
                        form.BeginInvoke((MethodInvoker)async delegate
                        {
                            try
                            {
                                await InjectFirstRunAccessToken(core, url);
                            }
                            catch (Exception ex)
                            {
                                Console.WriteLine("Could not pre-fill the access password: " + ex.Message);
                            }
                            core.Navigate(url);
                        });
                    }
                    catch (InvalidOperationException)
                    {
                        // 窗口已经关了。
                    }
                };
            }
            catch (Exception ex)
            {
                Console.WriteLine("App window unavailable: " + ex.Message);
                form.Close();
            }
            finally
            {
                settled();
            }
        };
        form.FormClosed += delegate
        {
            appWindowNavigate = null;
            if (ready)
            {
                RequestShutdown();
            }
        };
        Application.Run(form);
    }

    private static void ConfigureAppWindow(Form form, CoreWebView2 core)
    {
        core.Settings.IsStatusBarEnabled = false;
        core.ServerCertificateErrorDetected += delegate(
            object sender,
            CoreWebView2ServerCertificateErrorDetectedEventArgs args
        )
        {
            // 与启动器自己的本机请求同一规则：只信任指纹与本地记录一致的自签证书。
            // 事件处理器里抛异常会让导航直接失败，所以这里逐层兜住并记日志。
            bool pinned = false;
            try
            {
                pinned = ValidateLocalCertificate(
                    args.ServerCertificate == null
                        ? null
                        : args.ServerCertificate.ToX509Certificate2(),
                    SslPolicyErrors.RemoteCertificateChainErrors
                );
            }
            catch (Exception ex)
            {
                Console.WriteLine("Could not check the local certificate: " + ex.Message);
            }
            if (!pinned)
            {
                Console.WriteLine(
                    "Refused a certificate for " + args.RequestUri + " (" + args.ErrorStatus + ")."
                );
            }
            args.Action = pinned
                ? CoreWebView2ServerCertificateErrorAction.AlwaysAllow
                : CoreWebView2ServerCertificateErrorAction.Cancel;
        };
        core.NewWindowRequested += delegate(
            object sender,
            CoreWebView2NewWindowRequestedEventArgs args
        )
        {
            // 新窗口（分享链接、外部文档）交给默认浏览器，应用窗口只承载 DiceFrame 本身。
            args.Handled = true;
            OpenBrowser(args.Uri);
        };
        core.DocumentTitleChanged += delegate
        {
            string title = core.DocumentTitle;
            form.Text = string.IsNullOrEmpty(title) ? "DiceFrame" : title;
        };
    }

    private static async System.Threading.Tasks.Task InjectFirstRunAccessToken(
        CoreWebView2 core,
        string url
    )
    {
        string tokenFile = Path.Combine(launcherDataDir, "access_token.txt");
        if (!File.Exists(tokenFile))
        {
            return;
        }
        string token = File.ReadAllText(tokenFile, Encoding.UTF8).Trim();
        if (token.Length == 0)
        {
            return;
        }
        // 启用/关闭 HTTPS 会让页面在 http 与 https 之间跳转，而两者的 localStorage 不共用；
        // 按 host:port 匹配，跳转后仍然写得到密码（窗口只承载本机地址，外链交给浏览器）。
        Uri uri = new Uri(url);
        string hostPort = uri.Host + ":" + uri.Port;
        await core.AddScriptToExecuteOnDocumentCreatedAsync(
            "(function(){try{if(location.host!==\"" + JsonEscape(hostPort) + "\")return;"
                + "localStorage.setItem(\"" + FrontendAccessTokenKey + "\",\""
                + JsonEscape(token) + "\");}catch(e){}})();"
        );
    }

    // 应用窗口已就绪：与控制台脱离，控制台窗口随之关闭；之后的启动器输出写进 logs/launcher.log。
    private static void DetachConsole(string logsDir)
    {
        if (consoleDetached)
        {
            return;
        }
        try
        {
            Directory.CreateDirectory(logsDir);
            string logPath = Path.Combine(logsDir, "launcher.log");
            bool append = !File.Exists(logPath) || new FileInfo(logPath).Length < LauncherLogMaxBytes;
            StreamWriter writer = new StreamWriter(logPath, append, new UTF8Encoding(false));
            writer.AutoFlush = true;
            TextWriter log = TextWriter.Synchronized(writer);
            log.WriteLine();
            log.WriteLine("==== " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " app window ====");
            Console.SetOut(log);
            Console.SetError(log);
            FreeConsole();
            consoleDetached = true;
        }
        catch (Exception ex)
        {
            Console.WriteLine("Console stays open: " + ex.Message);
        }
    }

    // 控制台已经关掉时，用对话框告诉用户发生了什么。
    private static void NotifyUser(string message)
    {
        try
        {
            MessageBox.Show(message, "DiceFrame", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
        catch
        {
        }
    }

    // 与在控制台按 Ctrl+C 相同：让主循环结束，由 Main 的 finally / ProcessExit 收尾。
    private static void RequestShutdown()
    {
        shuttingDown = true;
        StopServer();
    }
}
