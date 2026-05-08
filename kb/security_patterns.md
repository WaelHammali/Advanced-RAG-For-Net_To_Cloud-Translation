# Security Patterns

- PCs are public by default for lab SSH access unless explicitly private.
- Each PC gets its own PEM file.
- Real routers use PC1.pem by default when PC1 exists.
- Real routers install/enable FRRouting by default.
- Real router EC2 size is selected by edge/interface count.
- Bastion receives SSH from admin CIDR.
- Private instances should not have public IPs unless explicitly requested.
- Private instances may allow SSH only from bastion SG.
- Firewall mode defaults to Security Group unless explicitly stated otherwise.
