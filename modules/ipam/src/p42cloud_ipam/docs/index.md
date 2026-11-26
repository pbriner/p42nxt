# IPAM

Your networks: **VLANs**, **subnets** (they don't overlap) and **IP addresses** (each knows its
subnet). A subnet's **Reserve the next free address** gives the first address nobody uses
(not the gateway, not in the DHCP range).

**DNS zones** are kept by hand, read from your UniFi console, or read from a DNS server in your
network by zone transfer, every night. An A or AAAA record points to its address, and so to the
item using it. Changes still happen on the DNS server: IPAM reads.
