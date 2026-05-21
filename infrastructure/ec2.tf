# ── Latest Ubuntu 22.04 LTS AMI (auto-resolves per region) ───────────────────
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# ── Security Group ────────────────────────────────────────────────────────────
resource "aws_security_group" "soko" {
  name        = "soko-sg"
  description = "Soko platform security group"
  vpc_id      = aws_vpc.soko.id

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.allowed_ssh_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "soko-sg" }
}

# ── IAM Role for EC2 (allows Secrets Manager + S3 access) ────────────────────
resource "aws_iam_role" "ec2" {
  name = "soko-ec2-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "ec2_secrets" {
  name = "soko-ec2-secrets-policy"
  role = aws_iam_role.ec2.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
        Resource = [
          aws_secretsmanager_secret.platform.arn,
          aws_secretsmanager_secret.ml.arn,
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:DeleteObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.images.arn,
          "${aws_s3_bucket.images.arn}/*",
        ]
      },
      {
        # Allow CloudWatch Logs for container monitoring
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

resource "aws_iam_instance_profile" "ec2" {
  name = "soko-ec2-profile"
  role = aws_iam_role.ec2.name
}

# ── EC2 Instance ──────────────────────────────────────────────────────────────
resource "aws_instance" "soko" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.soko.id]
  key_name               = var.ec2_key_name
  iam_instance_profile   = aws_iam_instance_profile.ec2.name

  root_block_device {
    volume_size           = 40  # GB — Docker images + Postgres volumes + ML models
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = false  # retain data on accidental instance stop
  }

  user_data = base64encode(templatefile("${path.module}/user_data.sh", {
    aws_region = var.aws_region
  }))

  tags = { Name = "soko-server" }

  lifecycle {
    # Prevent accidental replacement if AMI is updated
    ignore_changes = [ami, user_data]
  }
}

# ── Elastic IP (stable DNS target even after stop/start) ─────────────────────
resource "aws_eip" "soko" {
  instance = aws_instance.soko.id
  domain   = "vpc"
  tags     = { Name = "soko-eip" }
}
