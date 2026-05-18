variable "aws_region" {
  description = "AWS region — af-south-1 (Cape Town) is closest to Uganda"
  type        = string
  default     = "af-south-1"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "prod"
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.xlarge"
}

variable "ec2_key_name" {
  description = "Name of the EC2 key pair (must already exist in AWS)"
  type        = string
}

variable "allowed_ssh_cidr" {
  description = "CIDR block allowed to SSH into the EC2 instance. Use your own IP: x.x.x.x/32"
  type        = string
  default     = "0.0.0.0/0"
}

variable "domain_name" {
  description = "Your domain name (e.g. soko.ug). Used for CORS and PesaPal callback URLs."
  type        = string
  default     = ""
}

variable "alert_email" {
  description = "Email address for CloudWatch billing/infra alerts"
  type        = string
}

variable "github_repo" {
  description = "GitHub repo in owner/name format for OIDC trust (e.g. the-icemann/soko)"
  type        = string
  default     = "the-icemann/soko"
}
