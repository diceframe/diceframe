using System;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

// 覆盖升级：用户把新便携包直接解压、覆盖到安装目录。
//
// 应用内更新成功过一次后，程序住在 versions/v<版本>/，启动器按 data/_updater/current.json
// 只运行那一份；覆盖只换掉了根目录的 app/ 与 python/，启动器不会运行它们，
// MigrateLegacyPortablePayload 还会把它们当成旧布局残留删掉——覆盖等于白做。
// 覆盖时根目录的 DiceFrame.exe 也换成了新启动器，所以由新启动器在启动时认出
// 「根目录比当前版本新」的载荷，把它当成一次更新：搬进 versions/v<版本>/，
// 暂存同一个启动器，写与应用内更新相同的重启信号，交给 HandleUpdate 走健康检查、
// 观察期和失败回滚（回滚目标是覆盖前正在用的版本）。
//
// 根目录版本不比当前版本新（旧布局残留、同版本覆盖）、版本读不出来、或安装从没
// 用过 versions/（根目录本身就是活动版本，覆盖已经生效）时什么都不做，保持原有行为。
internal static partial class DiceFrameLauncher
{
    private static bool AdoptOverwrittenPayload(
        string installRoot,
        string updaterDir,
        string currentPointer,
        string restartSignal,
        string activeDir
    )
    {
        string versionsDir = Path.Combine(installRoot, "versions");
        if (
            !File.Exists(currentPointer)
            || File.Exists(restartSignal)
            || !HasPortablePayload(installRoot)
            || !IsUnder(activeDir, versionsDir)
        )
        {
            return false;
        }

        string version = ReadPayloadVersion(installRoot);
        string activeVersion = ReadPayloadVersion(activeDir);
        if (
            string.IsNullOrEmpty(version)
            || string.IsNullOrEmpty(activeVersion)
            || CompareVersions(version, activeVersion) <= 0
        )
        {
            return false;
        }
        string slot = VersionDirectoryName(version);
        if (slot == null)
        {
            return false;
        }
        string candidateDir = Path.Combine(versionsDir, slot);
        if (string.Equals(
            Path.GetFullPath(candidateDir),
            Path.GetFullPath(activeDir),
            StringComparison.OrdinalIgnoreCase
        ))
        {
            return false;
        }

        Console.WriteLine(
            "Found DiceFrame " + version + " unpacked over this install (running "
                + activeVersion + "); switching to it."
        );
        string installing = candidateDir + ".installing";
        try
        {
            // 同版本的旧槽位（例如上次覆盖升级失败回滚后留下的候选）不是活动版本，换成这次
            // 覆盖的文件。先改名挪开再删：.NET Framework 的 Directory.Delete 受 260 字符限制，
            // 深层文件删不掉会让整次接管失败；改名与目录深度无关。
            MoveAsideAndDelete(installing);
            MoveAsideAndDelete(candidateDir);
            Directory.CreateDirectory(installing);
            Directory.Move(
                Path.Combine(installRoot, "app"),
                Path.Combine(installing, "app")
            );
            Directory.Move(
                Path.Combine(installRoot, "python"),
                Path.Combine(installing, "python")
            );
            Directory.Move(installing, candidateDir);
        }
        catch (Exception ex)
        {
            // 搬到一半失败：已搬走的放回根目录，下次启动还能再试，不留半个版本目录。
            ReturnPayloadToRoot(installing, installRoot);
            Console.WriteLine("Could not adopt the unpacked version: " + ex.Message);
            return false;
        }

        try
        {
            string stagedLauncher = Path.Combine(updaterDir, "launcher-" + slot + ".exe");
            File.Copy(Path.Combine(installRoot, "DiceFrame.exe"), stagedLauncher, true);
            // 与应用内更新一致：候选进程启动时看到 restarting + 信号，launcher 写入结果后
            // 它会重新读取状态，界面直接显示更新完成或已回滚。
            WriteUpdateState(updaterDir, "restarting", version, "");
            AtomicWriteText(
                restartSignal,
                "{\n"
                    + "  \"schema\": 1,\n"
                    + "  \"expected_version\": \"" + JsonEscape(version) + "\",\n"
                    + "  \"candidate_dir\": \"" + JsonEscape(candidateDir) + "\",\n"
                    + "  \"launcher_path\": \"" + JsonEscape(stagedLauncher) + "\",\n"
                    + "  \"probation_seconds\": "
                    + ProbationDefaultSeconds.ToString(CultureInfo.InvariantCulture)
                    + ",\n"
                    + "  \"source\": \"overwrite\",\n"
                    + "  \"created_at\": "
                    + DateTimeOffset.UtcNow.ToUnixTimeSeconds().ToString(
                        CultureInfo.InvariantCulture
                    )
                    + "\n}\n"
            );
            return true;
        }
        catch (Exception ex)
        {
            TryDelete(restartSignal);
            ReturnPayloadToRoot(candidateDir, installRoot);
            Console.WriteLine("Could not prepare the unpacked version: " + ex.Message);
            return false;
        }
    }

    // 让出目录名：改名到 <名>.stale-<时间戳>，再尽力删除；删不掉（路径过深等）就留着，
    // 之后的版本清理会再试。改名失败才抛出——那说明这个位置确实不能用。
    private static void MoveAsideAndDelete(string directory)
    {
        if (!Directory.Exists(directory))
        {
            return;
        }
        string stale = directory + ".stale-"
            + DateTime.UtcNow.Ticks.ToString(CultureInfo.InvariantCulture);
        Directory.Move(directory, stale);
        try
        {
            Directory.Delete(stale, true);
        }
        catch (Exception ex)
        {
            Console.WriteLine("Left " + stale + " for later cleanup: " + ex.Message);
        }
    }

    // 把搬进候选目录的 app/、python/ 放回根目录并删掉候选目录；尽力而为，失败只提示。
    private static void ReturnPayloadToRoot(string stagedDir, string installRoot)
    {
        foreach (string name in new[] { "app", "python" })
        {
            try
            {
                string staged = Path.Combine(stagedDir, name);
                string root = Path.Combine(installRoot, name);
                if (Directory.Exists(staged) && !Directory.Exists(root))
                {
                    Directory.Move(staged, root);
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine("Could not restore " + name + ": " + ex.Message);
            }
        }
        try
        {
            if (Directory.Exists(stagedDir))
            {
                Directory.Delete(stagedDir, true);
            }
        }
        catch
        {
        }
    }

    // 读 app/src/version.py 的 __version__（与应用内更新器 _portable_payload_version 同一来源；
    // 候选的健康检查也返回这个值）。
    private static string ReadPayloadVersion(string directory)
    {
        try
        {
            string text = File.ReadAllText(
                Path.Combine(directory, "app", "src", "version.py"),
                Encoding.UTF8
            );
            Match match = Regex.Match(text, "__version__\\s*=\\s*[\"']([^\"']+)[\"']");
            if (match.Success)
            {
                return match.Groups[1].Value.Trim().TrimStart('v', 'V');
            }
        }
        catch
        {
        }
        return "";
    }

    // 与应用内更新器 _safe_version_dir 相同的槽位命名与字符约束。
    private static string VersionDirectoryName(string version)
    {
        return Regex.IsMatch(version ?? "", "^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")
            ? "v" + version
            : null;
    }

    // 语义化版本比较（x.y.z[-pre][+build]），返回负数 / 0 / 正数。
    // 核心段解析不了时返回 0：宁可不接管，也不猜哪边更新。
    private static int CompareVersions(string left, string right)
    {
        string leftPre;
        string rightPre;
        long[] leftCore = ParseVersionCore(left, out leftPre);
        long[] rightCore = ParseVersionCore(right, out rightPre);
        if (leftCore == null || rightCore == null)
        {
            return 0;
        }
        int length = Math.Max(leftCore.Length, rightCore.Length);
        for (int i = 0; i < length; i++)
        {
            long a = i < leftCore.Length ? leftCore[i] : 0;
            long b = i < rightCore.Length ? rightCore[i] : 0;
            if (a != b)
            {
                return a < b ? -1 : 1;
            }
        }
        if (leftPre == rightPre)
        {
            return 0;
        }
        // 正式版高于同号的预发布版：3.2.0 > 3.2.0-beta.1。
        if (leftPre.Length == 0)
        {
            return 1;
        }
        if (rightPre.Length == 0)
        {
            return -1;
        }
        string[] leftIds = leftPre.Split('.');
        string[] rightIds = rightPre.Split('.');
        for (int i = 0; i < Math.Min(leftIds.Length, rightIds.Length); i++)
        {
            long a;
            long b;
            bool aNumeric = long.TryParse(
                leftIds[i], NumberStyles.None, CultureInfo.InvariantCulture, out a
            );
            bool bNumeric = long.TryParse(
                rightIds[i], NumberStyles.None, CultureInfo.InvariantCulture, out b
            );
            int order;
            if (aNumeric && bNumeric)
            {
                order = a.CompareTo(b);
            }
            else if (aNumeric)
            {
                order = -1;
            }
            else if (bNumeric)
            {
                order = 1;
            }
            else
            {
                order = string.CompareOrdinal(leftIds[i], rightIds[i]);
            }
            if (order != 0)
            {
                return order < 0 ? -1 : 1;
            }
        }
        return leftIds.Length.CompareTo(rightIds.Length);
    }

    private static long[] ParseVersionCore(string version, out string prerelease)
    {
        string text = (version ?? "").Trim().TrimStart('v', 'V');
        int plus = text.IndexOf('+');
        if (plus >= 0)
        {
            text = text.Substring(0, plus);
        }
        int dash = text.IndexOf('-');
        prerelease = dash >= 0 ? text.Substring(dash + 1) : "";
        string core = dash >= 0 ? text.Substring(0, dash) : text;
        string[] parts = core.Split('.');
        long[] numbers = new long[parts.Length];
        for (int i = 0; i < parts.Length; i++)
        {
            if (!long.TryParse(
                parts[i], NumberStyles.None, CultureInfo.InvariantCulture, out numbers[i]
            ))
            {
                return null;
            }
        }
        return numbers;
    }
}
