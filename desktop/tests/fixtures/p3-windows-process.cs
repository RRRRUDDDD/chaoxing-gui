// Test-only process owner used by the P3 smoke runners. All termination uses
// retained handles or this owner's unnamed Job; no process-name/PID tree kills.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace Chaoxing.P3Smoke {
    public sealed class Identity {
        public uint pid;
        public string createdAtFileTime;
        public string executable;
        public bool inOuterJob;
        public bool alive;
        public uint? exitCode;
    }
    public sealed class Snapshot {
        public Identity host;
        public Identity[] active;
        public Identity[] observed;
        public bool outerJobHandleRetained;
    }
    public sealed class Cleanup {
        public bool fallbackUsed;
        public Identity[] before;
        public Identity[] remaining;
        public Identity[] observed;
    }

    public sealed class ProcessOwner : IDisposable {
        private IntPtr job = IntPtr.Zero;
        private IntPtr stdin = IntPtr.Zero;
        private IntPtr host = IntPtr.Zero;
        private uint hostPid;
        private readonly Dictionary<uint, IntPtr> handles = new Dictionary<uint, IntPtr>();
        private readonly Dictionary<uint, Identity> identities = new Dictionary<uint, Identity>();
        private Cleanup cleanup;

        private const uint QueryProcess = 0x1000, Synchronize = 0x00100000, Terminate = 1;
        private const uint WaitTimeout = 258;
        private static readonly IntPtr InvalidHandle = new IntPtr(-1);

        [StructLayout(LayoutKind.Sequential)] private struct SecurityAttributes { public int length; public IntPtr descriptor; [MarshalAs(UnmanagedType.Bool)] public bool inherit; }
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] private struct StartupInfo {
            public int cb; public string reserved; public string desktop; public string title;
            public uint x, y, xSize, ySize, xChars, yChars, fill, flags;
            public ushort show, reservedSize; public IntPtr reservedBytes, stdin, stdout, stderr;
        }
        [StructLayout(LayoutKind.Sequential)] private struct StartupInfoEx { public StartupInfo startup; public IntPtr attributes; }
        [StructLayout(LayoutKind.Sequential)] private struct ProcessInformation { public IntPtr process, thread; public uint pid, tid; }
        [StructLayout(LayoutKind.Sequential)] private struct BasicLimits {
            public long processTime, jobTime; public uint flags;
            public UIntPtr minWorkingSet, maxWorkingSet; public uint activeLimit;
            public UIntPtr affinity; public uint priority, scheduling;
        }
        [StructLayout(LayoutKind.Sequential)] private struct IoCounters { public ulong readOps, writeOps, otherOps, readBytes, writeBytes, otherBytes; }
        [StructLayout(LayoutKind.Sequential)] private struct ExtendedLimits {
            public BasicLimits basic; public IoCounters io;
            public UIntPtr processMemory, jobMemory, peakProcessMemory, peakJobMemory;
        }
        [StructLayout(LayoutKind.Sequential)] private struct FileTime { public uint low, high; }
        private delegate bool WindowCallback(IntPtr window, IntPtr extra);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern IntPtr CreateJobObject(IntPtr security, string name);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetInformationJobObject(IntPtr job, int kind, ref ExtendedLimits limits, uint size);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool QueryInformationJobObject(IntPtr job, int kind, IntPtr buffer, uint size, IntPtr returned);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool answer);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool TerminateJobObject(IntPtr job, uint code);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CreatePipe(out IntPtr read, out IntPtr write, ref SecurityAttributes attributes, uint size);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetHandleInformation(IntPtr handle, uint mask, uint flags);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern IntPtr CreateFile(string path, uint access, uint share, ref SecurityAttributes security, uint creation, uint attributes, IntPtr template);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, uint flags, ref IntPtr size);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attribute, IntPtr value, IntPtr size, IntPtr previous, IntPtr returned);
        [DllImport("kernel32.dll")] private static extern void DeleteProcThreadAttributeList(IntPtr list);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern bool CreateProcess(string app, StringBuilder command, IntPtr processSecurity, IntPtr threadSecurity, bool inherit, uint flags, IntPtr environment, string cwd, ref StartupInfoEx startup, out ProcessInformation process);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint ResumeThread(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetProcessTimes(IntPtr process, out FileTime created, out FileTime exited, out FileTime kernel, out FileTime user);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetExitCodeProcess(IntPtr process, out uint code);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder image, ref uint length);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool TerminateProcess(IntPtr process, uint code);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint WaitForSingleObject(IntPtr handle, uint timeout);
        [DllImport("user32.dll")] private static extern bool EnumWindows(WindowCallback callback, IntPtr extra);
        [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowText(IntPtr window, StringBuilder text, int max);
        [DllImport("user32.dll", SetLastError = true)] private static extern bool PostMessage(IntPtr window, uint message, IntPtr wparam, IntPtr lparam);

        private static void Check(bool ok, string operation) {
            if (!ok) throw new Win32Exception(Marshal.GetLastWin32Error(), operation);
        }
        private static void Close(ref IntPtr handle) {
            if (handle != IntPtr.Zero && handle != InvalidHandle) CloseHandle(handle);
            handle = IntPtr.Zero;
        }
        private static string Quote(string value) {
            var output = new StringBuilder("\"");
            int slashes = 0;
            foreach (char c in value) {
                if (c == '\\') { slashes++; continue; }
                output.Append('\\', slashes * (c == '"' ? 2 : 1));
                if (c == '"') output.Append('\\');
                output.Append(c); slashes = 0;
            }
            output.Append('\\', slashes * 2); output.Append('"');
            return output.ToString();
        }

        // NSIS consumes /D= and _?= as an unquoted remainder of the command
        // line. A generic raw command line is deliberately not exposed.
        public static string FormatNsisCommand(string executable, string[] args, string mode, string directory) {
            string[] expected = mode == "install" ? new[] { "/S", "/NS" } : mode == "uninstall" ? new[] { "/S" } : null;
            if (expected == null || args == null || !args.SequenceEqual(expected)) throw new ArgumentException("Unsupported NSIS options");
            if (directory == null || directory.Length < 4 || !char.IsLetter(directory[0]) || directory[1] != ':' || directory[2] != '\\'
                || directory.Any(char.IsControl) || directory.IndexOfAny(new[] { '"', '<', '>', '|', '?', '*' }) >= 0
                || directory.Substring(2).Contains(":") || directory.EndsWith("\\")
                || !string.Equals(Path.GetFullPath(directory), directory, StringComparison.OrdinalIgnoreCase)
                || string.Equals(Path.GetPathRoot(directory), directory, StringComparison.OrdinalIgnoreCase)) throw new ArgumentException("Unsafe NSIS directory path");
            return Quote(executable) + " " + string.Join(" ", expected) + (mode == "install" ? " /D=" : " _?=") + directory;
        }

        public Identity Start(string executable, string[] args, string cwd, IDictionary<string, string> environment, string stdoutPath, string stderrPath) {
            return Start(executable, args, cwd, environment, stdoutPath, stderrPath, null, null);
        }
        public Identity Start(string executable, string[] args, string cwd, IDictionary<string, string> environment, string stdoutPath, string stderrPath, string nsisMode, string nsisDirectory) {
            if (job != IntPtr.Zero || cleanup != null) throw new InvalidOperationException("ProcessOwner cannot be reused");
            IntPtr inputRead = IntPtr.Zero, output = IntPtr.Zero, error = IntPtr.Zero;
            IntPtr attributes = IntPtr.Zero, inherited = IntPtr.Zero, envBlock = IntPtr.Zero;
            bool initialized = false;
            ProcessInformation info = new ProcessInformation();
            try {
                job = CreateJobObject(IntPtr.Zero, null);
                Check(job != IntPtr.Zero, "CreateJobObject");
                var limits = new ExtendedLimits(); limits.basic.flags = 0x2000; // KILL_ON_JOB_CLOSE, no breakaway.
                Check(SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf(typeof(ExtendedLimits))), "SetInformationJobObject");
                var security = new SecurityAttributes { length = Marshal.SizeOf(typeof(SecurityAttributes)), inherit = true };
                Check(CreatePipe(out inputRead, out stdin, ref security, 0), "CreatePipe(stdin)");
                Check(SetHandleInformation(stdin, 1, 0), "SetHandleInformation(stdin writer)");
                output = CreateFile(stdoutPath, 0x40000000, 3, ref security, 2, 0x80, IntPtr.Zero);
                Check(output != InvalidHandle, "CreateFile(stdout)");
                error = CreateFile(stderrPath, 0x40000000, 3, ref security, 2, 0x80, IntPtr.Zero);
                Check(error != InvalidHandle, "CreateFile(stderr)");

                IntPtr required = IntPtr.Zero;
                InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref required);
                attributes = Marshal.AllocHGlobal(required);
                Check(InitializeProcThreadAttributeList(attributes, 1, 0, ref required), "InitializeProcThreadAttributeList");
                initialized = true;
                inherited = Marshal.AllocHGlobal(IntPtr.Size * 3);
                Marshal.WriteIntPtr(inherited, 0, inputRead);
                Marshal.WriteIntPtr(inherited, IntPtr.Size, output);
                Marshal.WriteIntPtr(inherited, IntPtr.Size * 2, error);
                Check(UpdateProcThreadAttribute(attributes, 0, new IntPtr(0x20002), inherited, new IntPtr(IntPtr.Size * 3), IntPtr.Zero, IntPtr.Zero), "UpdateProcThreadAttribute(handle list)");

                string block = string.Join("\0", environment.OrderBy(item => item.Key, StringComparer.OrdinalIgnoreCase).Select(item => item.Key + "=" + item.Value)) + "\0\0";
                envBlock = Marshal.StringToHGlobalUni(block);
                var startup = new StartupInfoEx { attributes = attributes };
                startup.startup.cb = Marshal.SizeOf(typeof(StartupInfoEx));
                startup.startup.flags = 0x101; // USESTDHANDLES | USESHOWWINDOW
                startup.startup.show = 0; // SW_HIDE
                startup.startup.stdin = inputRead; startup.startup.stdout = output; startup.startup.stderr = error;
                string command = nsisMode == null ? string.Join(" ", new[] { executable }.Concat(args ?? new string[0]).Select(Quote))
                    : FormatNsisCommand(executable, args, nsisMode, nsisDirectory);
                Check(CreateProcess(executable, new StringBuilder(command), IntPtr.Zero, IntPtr.Zero, true,
                    0x08080404, envBlock, cwd, ref startup, out info), "CreateProcess suspended");
                host = info.process; hostPid = info.pid;
                // The first instruction of the real host executes inside this Job.
                Check(AssignProcessToJobObject(job, host), "AssignProcessToJobObject(real host)");
                handles.Add(hostPid, host);
                identities.Add(hostPid, ReadIdentity(hostPid, host));
                Check(ResumeThread(info.thread) != uint.MaxValue, "ResumeThread(real host)");
                return Refresh(hostPid);
            } catch {
                if (info.process != IntPtr.Zero) {
                    TerminateProcess(info.process, 198);
                    WaitForSingleObject(info.process, 5000);
                    if (!handles.ContainsKey(info.pid)) CloseHandle(info.process);
                }
                Dispose();
                throw;
            } finally {
                Close(ref info.thread); Close(ref inputRead); Close(ref output); Close(ref error);
                if (initialized) DeleteProcThreadAttributeList(attributes);
                if (attributes != IntPtr.Zero) Marshal.FreeHGlobal(attributes);
                if (inherited != IntPtr.Zero) Marshal.FreeHGlobal(inherited);
                if (envBlock != IntPtr.Zero) Marshal.FreeHGlobal(envBlock);
            }
        }

        private Identity ReadIdentity(uint pid, IntPtr handle) {
            FileTime created, exited, kernel, user;
            Check(GetProcessTimes(handle, out created, out exited, out kernel, out user), "GetProcessTimes");
            var image = new StringBuilder(32768); uint length = (uint)image.Capacity;
            Check(QueryFullProcessImageName(handle, 0, image, ref length), "QueryFullProcessImageName");
            bool member; Check(IsProcessInJob(handle, job, out member), "IsProcessInJob");
            if (!member) throw new InvalidOperationException("Refusing a process outside the captured Job");
            return new Identity { pid = pid, createdAtFileTime = (((ulong)created.high << 32) | created.low).ToString(), executable = image.ToString(), inOuterJob = member };
        }
        private Identity Refresh(uint pid) {
            var source = identities[pid];
            bool alive = WaitForSingleObject(handles[pid], 0) == WaitTimeout;
            uint code; Check(GetExitCodeProcess(handles[pid], out code), "GetExitCodeProcess");
            return new Identity { pid = source.pid, createdAtFileTime = source.createdAtFileTime,
                executable = source.executable, inOuterJob = source.inOuterJob,
                alive = alive, exitCode = alive ? (uint?)null : code };
        }
        private uint[] Members() {
            if (job == IntPtr.Zero) return new uint[0];
            for (int capacity = 64; capacity <= 65536; capacity *= 2) {
                int bytes = 8 + IntPtr.Size * capacity;
                IntPtr buffer = Marshal.AllocHGlobal(bytes);
                try {
                    if (!QueryInformationJobObject(job, 3, buffer, (uint)bytes, IntPtr.Zero)) {
                        if (Marshal.GetLastWin32Error() == 234) continue;
                        Check(false, "QueryInformationJobObject(process list)");
                    }
                    int count = Marshal.ReadInt32(buffer, 4);
                    var pids = new uint[count];
                    for (int index = 0; index < count; index++) pids[index] = (uint)Marshal.ReadIntPtr(buffer, 8 + IntPtr.Size * index).ToInt64();
                    return pids;
                } finally { Marshal.FreeHGlobal(buffer); }
            }
            throw new InvalidOperationException("Unexpectedly large smoke process tree");
        }
        public Snapshot Inspect() {
            var active = new List<Identity>();
            foreach (uint pid in Members()) {
                if (!handles.ContainsKey(pid)) {
                    IntPtr handle = OpenProcess(QueryProcess | Synchronize | Terminate, false, pid);
                    if (handle == IntPtr.Zero) {
                        if (Marshal.GetLastWin32Error() == 87) continue; // Already exited.
                        Check(false, "OpenProcess(captured Job member)");
                    }
                    try {
                        var identity = ReadIdentity(pid, handle);
                        handles.Add(pid, handle); identities.Add(pid, identity);
                    } catch { CloseHandle(handle); throw; }
                }
                var current = Refresh(pid);
                if (current.alive) active.Add(current);
            }
            return new Snapshot { host = identities.ContainsKey(hostPid) ? Refresh(hostPid) : null,
                active = active.ToArray(), observed = identities.Keys.Select(Refresh).ToArray(), outerJobHandleRetained = job != IntPtr.Zero };
        }
        public void CloseStdin() { Close(ref stdin); }
        public void KillHost() {
            if (host != IntPtr.Zero && WaitForSingleObject(host, 0) == WaitTimeout) Check(TerminateProcess(host, 197), "TerminateProcess(captured host)");
        }
        public void KillMember(uint pid, string createdAtFileTime) {
            if (!identities.ContainsKey(pid) || identities[pid].createdAtFileTime != createdAtFileTime) throw new InvalidOperationException("Uncaptured process identity");
            if (Refresh(pid).alive) Check(TerminateProcess(handles[pid], 196), "TerminateProcess(captured member)");
        }
        public int CloseWindow(string title) {
            if (string.IsNullOrEmpty(title)) throw new ArgumentException("An exact title is required");
            if (host == IntPtr.Zero || WaitForSingleObject(host, 0) != WaitTimeout) return 0;
            int count = 0;
            EnumWindows((window, extra) => {
                uint pid; GetWindowThreadProcessId(window, out pid);
                if (pid == hostPid) {
                    var text = new StringBuilder(1024); GetWindowText(window, text, text.Capacity);
                    if (text.ToString() == title && PostMessage(window, 0x0010, IntPtr.Zero, IntPtr.Zero)) count++;
                }
                return true;
            }, IntPtr.Zero);
            return count;
        }
        public Cleanup Finish() {
            if (cleanup != null) return cleanup;
            var before = Inspect();
            bool fallback = before.active.Length != 0;
            if (fallback) Check(TerminateJobObject(job, 195), "TerminateJobObject(captured tree fallback)");
            CloseStdin();
            DateTime deadline = DateTime.UtcNow.AddSeconds(5);
            Snapshot after;
            do {
                after = Inspect();
                if (after.active.Length == 0 && after.observed.All(identity => !identity.alive)) break;
                Thread.Sleep(50);
            } while (DateTime.UtcNow < deadline);
            cleanup = new Cleanup { fallbackUsed = fallback, before = before.active,
                remaining = after.observed.Where(identity => identity.alive).ToArray(), observed = after.observed };
            return cleanup;
        }
        public void Dispose() {
            try { if (job != IntPtr.Zero) Finish(); }
            finally {
                Close(ref stdin); Close(ref job);
                foreach (IntPtr handle in handles.Values) CloseHandle(handle);
                handles.Clear(); host = IntPtr.Zero;
            }
        }
    }
}
