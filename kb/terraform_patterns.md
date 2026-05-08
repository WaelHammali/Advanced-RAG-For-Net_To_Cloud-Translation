# Terraform Patterns

## Public subnet
- aws_internet_gateway
- public route table with 0.0.0.0/0 to IGW

## Private subnet with outbound internet
- aws_eip
- aws_nat_gateway in public subnet
- private route table with 0.0.0.0/0 to NAT

## Real router appliance
- aws_instance for each real router
- source_dest_check = false
- primary subnet_id on first router LAN subnet
- aws_network_interface for extra router LAN interfaces when needed
- aws_network_interface_attachment for secondary ENIs
- user_data installs/enables FRRouting and Linux IP forwarding by default
- instance type is chosen from router edge/interface count:
  - 1-2 edges/interfaces: t3.micro
  - 3 edges/interfaces: t3.small
  - 4-5 edges/interfaces: t3.medium
  - 6+ edges/interfaces: t3.large

## Peering
- aws_vpc_peering_connection
- per-subnet route entries to peer VPC CIDR

## Transit Gateway
- aws_ec2_transit_gateway
- aws_ec2_transit_gateway_route_table
- VPC attachments
- route table association
- route table propagation
