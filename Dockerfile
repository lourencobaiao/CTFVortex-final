# CTF Challenge Docker Image
FROM ubuntu:22.04

# Prevent interactive prompts during installation
ENV DEBIAN_FRONTEND=noninteractive

# Install SSH server and basic tools
RUN apt-get update && apt-get install -y \
    openssh-server \
    sudo \
    vim \
    nano \
    curl \
    wget \
    netcat \
    python3 \
    python3-pip \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create SSH directory
RUN mkdir /var/run/sshd

# Create CTF user
RUN useradd -m -s /bin/bash sandboxuser && \
    echo "sandboxuser:sandboxpass" | chpasswd

# Allow SSH login with password
RUN sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin no/' /etc/ssh/sshd_config && \
    sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' /etc/ssh/sshd_config && \
    sed -i 's/UsePAM yes/UsePAM no/' /etc/ssh/sshd_config

# Create challenge directory structure
RUN mkdir -p /home/sandboxuser/t && \
    mkdir -p /home/sandboxuser/.hidden && \
    mkdir -p /opt/challenge

# Create the password file with the flag
RUN echo "flag{SuperSecretCTFPassword2024}" > /home/sandboxuser/t/password.txt && \
    chmod 644 /home/sandboxuser/t/password.txt

# Create additional challenge files (optional)
RUN echo "This is a decoy file" > /home/sandboxuser/.hidden/fake.txt && \
    echo "flag{this_is_a_real_flag}" > /opt/challenge/flag.txt && \
    chmod 400 /opt/challenge/flag.txt

# Set proper ownership
RUN chown -R sandboxuser:sandboxuser /home/sandboxuser

# Expose SSH port
EXPOSE 22

# Start SSH service
CMD ["/usr/sbin/sshd", "-D"]