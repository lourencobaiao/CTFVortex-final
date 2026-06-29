from abc import ABC, abstractmethod
import paramiko
import time


class TerminalConnection(ABC):
    """Abstract base class for terminal connections"""

    @abstractmethod
    def connect(self) -> None:
        """Establish connection to terminal"""
        pass

    @abstractmethod
    def execute_command(self, command: str) -> str:
        """Execute a command and return output"""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Close the connection"""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connection is active"""
        pass


class SSHTerminal(TerminalConnection):
    """SSH terminal connection implementation for CTF challenges"""

    def __init__(self, host: str, port: int, username: str, password: str):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.ssh = None
        self.channel = None
        self.command_count = 0

    def connect(self) -> None:
        """Connect to CTF challenge SSH server"""
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh.connect(self.host, port=self.port, username=self.username, password=self.password)
        self.channel = self.ssh.invoke_shell()

        print(f" Connected to CTF challenge at {self.host}:{self.port}")
        print(f" User: {self.username}\n")

        # Wait for initial prompt
        time.sleep(1)
        if self.channel.recv_ready():
            initial_output = self.channel.recv(4096).decode(errors="ignore")
            print(f"Initial banner:\n{initial_output}")

    def execute_command(self, command: str) -> str:
        """Execute command and return output"""
        if not self.is_connected():
            raise Exception("Not connected to terminal")

        self.command_count += 1
        self.channel.send(command + "\n")

        # Wait for command execution
        time.sleep(0.5)
        output = ""
        attempts = 0
        while attempts < 10:
            if self.channel.recv_ready():
                output += self.channel.recv(4096).decode(errors="ignore")
                time.sleep(0.1)
            else:
                attempts += 1
                time.sleep(0.1)

        # Clean output
        lines = output.splitlines()
        if len(lines) > 1:
            filtered_output = "\n".join(lines[1:-1]) if len(lines) > 2 else lines[1]
        else:
            filtered_output = output

        return filtered_output

    def disconnect(self) -> None:
        """Close SSH connection"""
        if self.channel:
            self.channel.close()
        if self.ssh:
            self.ssh.close()
        print(f"\n SSH session terminated. Total commands executed: {self.command_count}")

    def is_connected(self) -> bool:
        """Check if SSH connection is active"""
        return self.ssh is not None and self.ssh.get_transport() is not None and self.ssh.get_transport().is_active()
