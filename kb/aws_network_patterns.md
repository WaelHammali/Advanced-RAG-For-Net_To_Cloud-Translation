# AWS Network Patterns

## Public / Private
- Public subnet has route to Internet Gateway.
- Private subnet has no direct Internet Gateway route.
- Private outbound internet requires NAT Gateway in a public subnet.

## Peering
- VPC peering is non-transitive.
- Good for exactly two routed domains with direct connectivity.

## Transit Gateway
- Better for 3+ routed domains, chains, rings, star, mesh, and hub/spoke.
- Middle transit-only domains may need dedicated attachment subnets.

## Bastion
- Bastion host is public.
- Private hosts should allow SSH from bastion security group only.
