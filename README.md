# net2tf_v3

RAG-assisted compiler that translates natural-language network topologies into AWS logical design and Terraform code.

## Core model

This project follows the real routing behavior model:

- PC -> EC2
- Server -> EC2 by default
- Switch / LAN / VLAN -> Subnet
- Router -> VPC
- Firewall -> Security Group by default unless explicitly overridden
- Router-to-router link -> explicit inter-VPC connectivity

## Pipeline

User prompt  
-> topology extraction  
-> RAG retrieval  
-> planner  
-> deterministic compiler  
-> Terraform rendering  
-> quality checks  
-> spec-compliant rendered response
