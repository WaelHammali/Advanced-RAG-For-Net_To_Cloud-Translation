# Example: Two Routers

Topology:
PC1 -- SW1 -- R1 -- R2 -- SW2 -- PC2

Interpretation:
- R1 -> VPC 1 plus EC2-R1 router appliance.
- R2 -> VPC 2 plus EC2-R2 router appliance.
- SW1/SW2 -> subnets in their router VPCs.
- PC1/PC2 -> EC2 hosts in their LAN subnets.
- Use VPC peering for inter-VPC connectivity by default for exactly two routers.
- OSPF is not configured unless requested later by Ansible.
