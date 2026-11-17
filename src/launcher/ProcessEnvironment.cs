using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;

internal static partial class DiceFrameLauncher
{
    // Windows 环境块本身允许同时存在 HTTPS_PROXY / https_proxy，但 .NET Framework
    // 首次访问 ProcessStartInfo.EnvironmentVariables 时会把整个块复制进大小写不敏感
    // 字典，并在重复键上抛 ArgumentException。先只归一化真正的重复项，保留首个值；
    // 不记录变量值，也不对代理地址做特殊判断。
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr GetEnvironmentStrings();

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool FreeEnvironmentStrings(IntPtr environmentStrings);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool SetEnvironmentVariable(string name, string value);

    private static void NormalizeProcessEnvironment()
    {
        IntPtr block = GetEnvironmentStrings();
        if (block == IntPtr.Zero)
        {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }

        Dictionary<string, string> firstValues = new Dictionary<string, string>(
            StringComparer.OrdinalIgnoreCase
        );
        Dictionary<string, int> counts = new Dictionary<string, int>(
            StringComparer.OrdinalIgnoreCase
        );
        try
        {
            IntPtr current = block;
            while (true)
            {
                string entry = Marshal.PtrToStringUni(current);
                if (string.IsNullOrEmpty(entry))
                {
                    break;
                }
                current = new IntPtr(current.ToInt64() + (entry.Length + 1) * 2);
                int separator = entry.IndexOf('=');
                // 跳过 =C:=... 这类当前目录伪变量；普通变量的首个 = 分隔名和值。
                if (separator <= 0)
                {
                    continue;
                }
                string name = entry.Substring(0, separator);
                string value = entry.Substring(separator + 1);
                if (!firstValues.ContainsKey(name))
                {
                    firstValues.Add(name, value);
                    counts.Add(name, 1);
                }
                else
                {
                    counts[name] += 1;
                }
            }
        }
        finally
        {
            FreeEnvironmentStrings(block);
        }

        foreach (KeyValuePair<string, int> item in counts)
        {
            if (item.Value < 2)
            {
                continue;
            }
            // SetEnvironmentVariable 的名称匹配不区分大小写。按原始出现次数
            // 删除整组变体，再恢复首个值，结果在父进程与所有子进程中保持一致。
            for (int index = 0; index < item.Value; index++)
            {
                if (!SetEnvironmentVariable(item.Key, null))
                {
                    int error = Marshal.GetLastWin32Error();
                    const int ErrorEnvironmentVariableNotFound = 203;
                    if (error != ErrorEnvironmentVariableNotFound)
                    {
                        throw new Win32Exception(error);
                    }
                }
            }
            if (!SetEnvironmentVariable(item.Key, firstValues[item.Key]))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
        }
    }
}
