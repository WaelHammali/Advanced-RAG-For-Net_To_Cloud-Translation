# Example: Single Router

Topology:
PC1 -- SW1 -- R1

Interpretation:
- R1 -> one VPC plus one EC2 router appliance.
- SW1 -> one subnet inside the R1 VPC.
- PC1 -> EC2 in that subnet.
- R1 router appliance has an ENI in the SW1 subnet.
- R1 source_dest_check is false.
