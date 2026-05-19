terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Remote state — S3 bucket must exist before running `terraform init`
  # Run `scripts/bootstrap-tf-state.sh` once to create it.
  backend "s3" {
    bucket         = "soko-terraform-state-491085424720"
    key            = "prod/terraform.tfstate"
    region         = "af-south-1"
    use_lockfile   = true
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "soko"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# CloudFront ACM certificates must live in us-east-1 — uncomment when adding domain
# provider "aws" {
#   alias  = "us_east_1"
#   region = "us-east-1"
#
#   default_tags {
#     tags = {
#       Project     = "soko"
#       Environment = var.environment
#       ManagedBy   = "terraform"
#     }
#   }
# }
