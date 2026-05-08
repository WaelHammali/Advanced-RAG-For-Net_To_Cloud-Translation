# Real Router Appliance Rules

A real router in the source topology is both a cloud routing domain and a simulated router device.

## If router is absent

Do not invent a physical router appliance. Create a VPC only and place the architecture inside it.

## If router is present

For every explicit router component:

- Create a VPC for the routing domain.
- Create an EC2 router appliance in that VPC.
- Use one ENI/interface per connected LAN/subnet when possible.
- Put the primary ENI in the first router subnet.
- Put secondary ENIs in the remaining router subnets.
- Set `source_dest_check = false`.
- Use PC1.pem for router SSH management when PC1 exists.
- The router EC2 installs FRRouting and enables IP forwarding by default; OSPF networks/areas are configured later by Ansible when requested.

## Direct host links

If a router connects directly to a PC/server, the direct link is a separate LAN/subnet.

Example:

```text
PC1 -- SW1
PC2 -- SW1
SW1 -- R1
R1 -- PC3
```

AWS:

```text
VPC-R1
├── EC2-R1-router-appliance
├── Subnet-SW1: PC1, PC2, R1 ENI
└── Subnet-R1-DIRECT: PC3, R1 ENI
```
