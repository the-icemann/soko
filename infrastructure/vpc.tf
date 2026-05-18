resource "aws_vpc" "soko" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "soko-vpc" }
}

resource "aws_internet_gateway" "soko" {
  vpc_id = aws_vpc.soko.id
  tags   = { Name = "soko-igw" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.soko.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = "${var.aws_region}a"
  map_public_ip_on_launch = true

  tags = { Name = "soko-public-subnet" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.soko.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.soko.id
  }

  tags = { Name = "soko-public-rt" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}
