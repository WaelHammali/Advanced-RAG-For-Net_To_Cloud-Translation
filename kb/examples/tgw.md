# Example: Three Router Chain

Topology:
PC1 -- SW1 -- R1 -- R2 -- R3 -- SW3 -- PC3

Interpretation:
- 3 VPCs.
- R1/R2/R3 each get an EC2 router appliance.
- Use Transit Gateway for inter-VPC connectivity.
- Middle router domains may need a transit subnet for attachment.
- OSPF is Ansible-only.
