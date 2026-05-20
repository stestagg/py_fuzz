import Foundation
@preconcurrency import Virtualization

private struct CLIOptions {
    let imageDir: URL
    let cpuCount: Int
    let memoryMiB: UInt64
    let timeoutSeconds: TimeInterval?
    let command: String
    let envFile: String?
    let mounts: [MountShare]
    let interactive: Bool
    let dmesgPath: String?

    var kernelURL: URL { imageDir.appendingPathComponent("vmlinux") }
    var initrdURL: URL { imageDir.appendingPathComponent("initram") }
    var diskURL: URL { imageDir.appendingPathComponent("fs.img") }
}

private struct MountShare {
    let url: URL
    let name: String
    let readOnly: Bool
}

private enum CLIError: Error, CustomStringConvertible {
    case usage(String)

    var description: String {
        switch self {
        case .usage(let message):
            return message
        }
    }
}

private final class TerminalMode: @unchecked Sendable {
    static let shared = TerminalMode()

    private var originalAttributes = termios()
    private var didCaptureOriginal = false
    private var didEnterRawMode = false

    func enterRawMode() {
        guard isatty(FileHandle.standardInput.fileDescriptor) == 1 else {
            return
        }

        var attributes = termios()
        guard tcgetattr(FileHandle.standardInput.fileDescriptor, &attributes) == 0 else {
            return
        }

        originalAttributes = attributes
        didCaptureOriginal = true

        attributes.c_iflag &= ~tcflag_t(ICRNL)
        attributes.c_lflag &= ~tcflag_t(ICANON | ECHO)

        guard tcsetattr(FileHandle.standardInput.fileDescriptor, TCSANOW, &attributes) == 0 else {
            return
        }

        didEnterRawMode = true
    }

    func restore() {
        guard didCaptureOriginal, didEnterRawMode else {
            return
        }

        tcsetattr(FileHandle.standardInput.fileDescriptor, TCSANOW, &originalAttributes)
        didEnterRawMode = false
    }
}

private enum ProcessExit {
    static func exit(_ code: Int32) -> Never {
        TerminalMode.shared.restore()
        Foundation.exit(code)
    }
}

private final class VMDelegate: NSObject, VZVirtualMachineDelegate, @unchecked Sendable {
    private let onStop: () -> Void

    init(onStop: @escaping () -> Void) {
        self.onStop = onStop
    }

    func guestDidStop(_ virtualMachine: VZVirtualMachine) {
        onStop()
    }
}

private final class LinuxVirtRunner: @unchecked Sendable {
    private let options: CLIOptions
    private var virtualMachine: VZVirtualMachine?
    private var vmDelegate: VMDelegate?
    private var hasExited = false
    private var exitCodeOnStop: Int32 = EXIT_SUCCESS
    private var resizePipeWriter: FileHandle?
    private var sigwinchSource: DispatchSourceSignal?

    init(options: CLIOptions) {
        self.options = options
    }

    func run() throws -> Never {
        let configuration = VZVirtualMachineConfiguration()
        configuration.cpuCount = options.cpuCount
        configuration.memorySize = options.memoryMiB * 1024 * 1024
        configuration.serialPorts = createSerialPortConfiguration()
        configuration.bootLoader = createBootLoader()
        configuration.storageDevices = [try createDiskConfiguration()]
        configuration.directorySharingDevices = createDirectorySharingDevices()
        try configuration.validate()

        TerminalMode.shared.enterRawMode()

        DispatchQueue.main.async { [self] in
            startVirtualMachine(configuration: configuration)
        }

        dispatchMain()
    }

    private func startVirtualMachine(configuration: VZVirtualMachineConfiguration) {
        let vm = VZVirtualMachine(configuration: configuration)
        let delegate = VMDelegate { [weak self] in
            self?.finishFromGuestStop()
        }

        vm.delegate = delegate
        virtualMachine = vm
        vmDelegate = delegate

        vm.start { [weak self] result in
            guard let self else {
                return
            }

            switch result {
            case .success:
                self.scheduleTimeoutIfNeeded()
            case .failure(let error):
                writeStderr("Failed to start the virtual machine: \(error)\n")
                ProcessExit.exit(EXIT_FAILURE)
            }
        }
    }

    private func scheduleTimeoutIfNeeded() {
        guard let timeoutSeconds = options.timeoutSeconds else {
            return
        }

        DispatchQueue.main.asyncAfter(deadline: .now() + timeoutSeconds) { [weak self] in
            guard let self, !self.hasExited else {
                return
            }

            self.exitCodeOnStop = 124
            writeStderr("VM timed out after \(formatTimeout(timeoutSeconds)) seconds. Stopping.\n")

            if self.virtualMachine?.state == .running {
                self.virtualMachine?.stop { error in
                    if let error {
                        writeStderr("Failed to stop the virtual machine after timeout: \(error)\n")
                    }
                    self.finish(code: 124)
                }

                DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { [weak self] in
                    guard let self, !self.hasExited else {
                        return
                    }
                    self.finish(code: 124)
                }
            } else {
                self.finish(code: 124)
            }
        }
    }

    private func finishFromGuestStop() {
        finish(code: exitCodeOnStop)
    }

    private func finish(code: Int32) -> Never {
        if hasExited {
            dispatchMain()
        }

        hasExited = true
        ProcessExit.exit(code)
    }

    private func createSerialPortConfiguration() -> [VZSerialPortConfiguration] {
        let hvc0 = VZVirtioConsoleDeviceSerialPortConfiguration()
        let hvc1 = VZVirtioConsoleDeviceSerialPortConfiguration()
        let nullRead = FileHandle(forReadingAtPath: "/dev/null")!

        // hvc0 write side: boot/kernel console output.
        let hvc0WriteHandle: FileHandle
        if let dmesgPath = options.dmesgPath {
            FileManager.default.createFile(atPath: dmesgPath, contents: nil)
            if let dmesgHandle = FileHandle(forWritingAtPath: dmesgPath) {
                hvc0WriteHandle = dmesgHandle
            } else {
                writeStderr("Warning: could not open dmesg path for writing: \(dmesgPath)\n")
                hvc0WriteHandle = FileHandle(forWritingAtPath: "/dev/null")!
            }
        } else {
            // Relay boot/kernel noise to stdout with dim/grey ANSI codes.
            let greyPipe = Pipe()
            hvc0WriteHandle = greyPipe.fileHandleForWriting
            startGreyRelay(from: greyPipe.fileHandleForReading)
        }

        // hvc0 read side: in interactive mode use a resize pipe so that SIGWINCH events
        // can be forwarded to the guest as "R:rows:cols\n" messages that pfm-run applies
        // with stty. Non-interactive sessions don't need resize signalling.
        let hvc0ReadHandle: FileHandle
        if options.interactive {
            let resizePipe = Pipe()
            resizePipeWriter = resizePipe.fileHandleForWriting
            hvc0ReadHandle = resizePipe.fileHandleForReading
            setupSIGWINCH()
        } else {
            hvc0ReadHandle = nullRead
        }

        hvc0.attachment = VZFileHandleSerialPortAttachment(
            fileHandleForReading: hvc0ReadHandle,
            fileHandleForWriting: hvc0WriteHandle
        )

        // hvc1: [PFM] messages + command output (non-interactive) or interactive shell I/O.
        // In interactive mode, pfm-run opens /dev/hvc1 *inside* the setsid'd new session so
        // the kernel automatically assigns it as the controlling terminal — no cttyhack needed.
        hvc1.attachment = VZFileHandleSerialPortAttachment(
            fileHandleForReading: options.interactive ? FileHandle.standardInput : nullRead,
            fileHandleForWriting: FileHandle.standardOutput
        )

        return [hvc0, hvc1]
    }

    private func setupSIGWINCH() {
        signal(SIGWINCH, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: SIGWINCH, queue: .main)
        source.setEventHandler { [weak self] in
            self?.sendResizeToGuest()
        }
        source.resume()
        sigwinchSource = source
    }

    private func sendResizeToGuest() {
        guard let writer = resizePipeWriter else { return }
        var ws = winsize()
        guard ioctl(FileHandle.standardOutput.fileDescriptor, TIOCGWINSZ, &ws) == 0,
              ws.ws_row > 0, ws.ws_col > 0 else { return }
        let msg = "R:\(ws.ws_row):\(ws.ws_col)\n"
        if let data = msg.data(using: .utf8) {
            writer.write(data)
        }
    }

    private func startGreyRelay(from handle: FileHandle) {
        let dim = "\u{1B}[2m".data(using: .utf8)!
        let reset = "\u{1B}[0m".data(using: .utf8)!
        let stdout = FileHandle.standardOutput
        DispatchQueue.global(qos: .utility).async {
            while true {
                let chunk = handle.availableData
                if chunk.isEmpty { break }
                var out = Data()
                out.append(dim)
                out.append(chunk)
                out.append(reset)
                stdout.write(out)
            }
        }
    }

    private func createDiskConfiguration() throws -> VZStorageDeviceConfiguration {
        let attachment = try VZDiskImageStorageDeviceAttachment(url: options.diskURL, readOnly: true)
        return VZVirtioBlockDeviceConfiguration(attachment: attachment)
    }

    private func createDirectorySharingDevices() -> [VZDirectorySharingDeviceConfiguration] {
        guard !options.mounts.isEmpty else {
            return []
        }

        var directoriesToShare: [String: VZSharedDirectory] = [:]
        for mount in options.mounts {
            directoriesToShare[mount.name] = VZSharedDirectory(url: mount.url, readOnly: mount.readOnly)
        }

        let share = VZMultipleDirectoryShare(directories: directoriesToShare)
        let configuration = VZVirtioFileSystemDeviceConfiguration(tag: "pfrun")
        configuration.share = share
        return [configuration]
    }

    private func createBootLoader() -> VZBootLoader {
        let bootLoader = VZLinuxBootLoader(kernelURL: options.kernelURL)
        bootLoader.initialRamdiskURL = options.initrdURL

        let script = Data(options.command.utf8).base64EncodedString()
        var cmdlineParts = [
            "console=hvc0",
            "root=fe00",
            "ro",
            "pfmscript=\(script)",
        ]
        if let envFile = options.envFile {
            cmdlineParts.append("pfmenv=\(envFile)")
        }
        if options.interactive {
            cmdlineParts.append("pfminteractive=1")
            var ws = winsize()
            if ioctl(FileHandle.standardOutput.fileDescriptor, TIOCGWINSZ, &ws) == 0,
               ws.ws_row > 0, ws.ws_col > 0 {
                cmdlineParts.append("pfmrows=\(ws.ws_row)")
                cmdlineParts.append("pfmcols=\(ws.ws_col)")
            }
        }
        bootLoader.commandLine = cmdlineParts.joined(separator: " ")

        return bootLoader
    }
}

private func parseOptions(arguments: [String]) throws -> CLIOptions {
    var imageDir: String?
    var cpuCount: Int?
    var memoryMiB: UInt64?
    var timeoutSeconds: TimeInterval?
    var command: String?
    var envFile: String?
    var mounts: [MountShare] = []
    var mountNames = Set<String>()
    var interactive = false
    var dmesgPath: String?

    var index = 1
    while index < arguments.count {
        let argument = arguments[index]

        if argument == "--help" || argument == "-h" {
            throw CLIError.usage("")
        }

        if argument == "--interactive" {
            interactive = true
            index += 1
            continue
        }

        let key: String
        let value: String

        if let equalsIndex = argument.firstIndex(of: "=") {
            key = String(argument[..<equalsIndex])
            value = String(argument[argument.index(after: equalsIndex)...])
        } else {
            key = argument
            index += 1
            guard index < arguments.count else {
                throw CLIError.usage("Missing value for \(argument).")
            }
            value = arguments[index]
        }

        switch key {
        case "--imagedir":
            imageDir = value
        case "--ncpu":
            guard let parsed = Int(value), parsed > 0 else {
                throw CLIError.usage("--ncpu must be a positive integer.")
            }
            cpuCount = parsed
        case "--mem":
            guard let parsed = UInt64(value), parsed > 0 else {
                throw CLIError.usage("--mem must be a positive integer number of MiB.")
            }
            memoryMiB = parsed
        case "--timeout":
            guard let parsed = TimeInterval(value), parsed > 0 else {
                throw CLIError.usage("--timeout must be a positive number of seconds.")
            }
            timeoutSeconds = parsed
        case "--cmd":
            guard !value.isEmpty else {
                throw CLIError.usage("--cmd must not be empty.")
            }
            command = value
        case "--env-file":
            guard !value.isEmpty else {
                throw CLIError.usage("--env-file must not be empty.")
            }
            envFile = value
        case "--mount":
            let mount = try parseMount(value, readOnly: true)
            guard mountNames.insert(mount.name).inserted else {
                throw CLIError.usage("Duplicate mount name: \(mount.name).")
            }
            mounts.append(mount)
        case "--mount-rw":
            let mount = try parseMount(value, readOnly: false)
            guard mountNames.insert(mount.name).inserted else {
                throw CLIError.usage("Duplicate mount name: \(mount.name).")
            }
            mounts.append(mount)
        case "--dmesg":
            guard !value.isEmpty else {
                throw CLIError.usage("--dmesg must not be empty.")
            }
            dmesgPath = value
        default:
            throw CLIError.usage("Unknown argument: \(key).")
        }

        index += 1
    }

    guard let imageDir else {
        throw CLIError.usage("Missing required argument: --imagedir.")
    }
    guard let cpuCount else {
        throw CLIError.usage("Missing required argument: --ncpu.")
    }
    guard let memoryMiB else {
        throw CLIError.usage("Missing required argument: --mem.")
    }
    guard let command else {
        throw CLIError.usage("Missing required argument: --cmd.")
    }

    let imageDirURL = URL(fileURLWithPath: imageDir, isDirectory: true)
        .standardizedFileURL

    let options = CLIOptions(
        imageDir: imageDirURL,
        cpuCount: cpuCount,
        memoryMiB: memoryMiB,
        timeoutSeconds: timeoutSeconds,
        command: command,
        envFile: envFile,
        mounts: mounts,
        interactive: interactive,
        dmesgPath: dmesgPath
    )

    try validateImages(options: options)
    return options
}

private func parseMount(_ value: String, readOnly: Bool) throws -> MountShare {
    guard let separatorIndex = value.firstIndex(of: ":") else {
        throw CLIError.usage("Mount must be in the form RELPATH:NAME.")
    }

    let path = String(value[..<separatorIndex])
    let name = String(value[value.index(after: separatorIndex)...])

    guard !path.isEmpty else {
        throw CLIError.usage("Mount path must not be empty.")
    }
    guard !name.isEmpty else {
        throw CLIError.usage("Mount name must not be empty.")
    }

    let url = URL(fileURLWithPath: path, isDirectory: true)
        .standardizedFileURL

    return MountShare(url: url, name: name, readOnly: readOnly)
}

private func validateImages(options: CLIOptions) throws {
    var isDirectory: ObjCBool = false
    guard FileManager.default.fileExists(atPath: options.imageDir.path, isDirectory: &isDirectory), isDirectory.boolValue else {
        throw CLIError.usage("--imagedir does not exist or is not a directory: \(options.imageDir.path)")
    }

    for url in [options.diskURL, options.initrdURL, options.kernelURL] {
        guard FileManager.default.isReadableFile(atPath: url.path) else {
            throw CLIError.usage("Required image file is missing or not readable: \(url.path)")
        }
    }

    for mount in options.mounts {
        isDirectory = false
        guard FileManager.default.fileExists(atPath: mount.url.path, isDirectory: &isDirectory), isDirectory.boolValue else {
            throw CLIError.usage("Mount path does not exist or is not a directory: \(mount.url.path)")
        }
        guard FileManager.default.isReadableFile(atPath: mount.url.path) else {
            throw CLIError.usage("Mount path is not readable: \(mount.url.path)")
        }
    }
}

private func usage() -> String {
    """
    Usage: pfrun --imagedir PATH --ncpu N --mem N --cmd CMD [--timeout S] [--env-file PATH] [--dmesg PATH] [--mount RELPATH:NAME] [--mount-rw RELPATH:NAME]

      --imagedir PATH  Directory containing fs.img, initram, and vmlinux.
      --ncpu N         Number of virtual CPUs.
      --mem N          Memory in MiB.
      --cmd CMD        Guest command/script passed as pfmscript.
      --timeout S      Optional timeout in seconds.
      --env-file PATH  Path to an env file inside the VM (lines: export VAR=VAL). Optional.
      --interactive    Run interactively: adds pfminteractive=1 to kernel cmdline so
                       pfm-run sets up a controlling terminal via cttyhack.
      --dmesg PATH     Write kernel/boot console output (hvc0) to this file instead of
                       relaying it to stdout with dim ANSI codes.
      --mount P:N      Expose relative directory path P as read-only share N. Repeatable.
      --mount-rw P:N   Expose relative directory path P as read-write share N. Repeatable.

    Mounts are exposed to the VM through VirtioFS tag "pfrun"; guest-side mounting is not performed by pfrun.
    """
}

private func writeStderr(_ string: String) {
    if let data = string.data(using: .utf8) {
        FileHandle.standardError.write(data)
    }
}

private func formatTimeout(_ timeout: TimeInterval) -> String {
    if timeout.rounded() == timeout {
        return String(Int(timeout))
    }

    return String(timeout)
}

do {
    let options = try parseOptions(arguments: CommandLine.arguments)
    try LinuxVirtRunner(options: options).run()
} catch let error as CLIError {
    if !error.description.isEmpty {
        writeStderr("\(error.description)\n\n")
    }
    writeStderr("\(usage())\n")
    ProcessExit.exit(2)
} catch {
    writeStderr("\(error)\n")
    ProcessExit.exit(EXIT_FAILURE)
}
