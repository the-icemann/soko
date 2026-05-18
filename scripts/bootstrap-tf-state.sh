#!/usr/bin/env bash
# Run ONCE before `terraform init` to create the S3 + DynamoDB Terraform backend.
# After this runs, commit infrastructure/ and push — Terraform state lives in S3.

set -euo pipefail

REGION="af-south-1"
BUCKET="soko-terraform-state"
TABLE="soko-terraform-locks"

echo "Creating Terraform state bucket: $BUCKET"
aws s3api create-bucket \
  --bucket "$BUCKET" \
  --region "$REGION" \
  --create-bucket-configuration LocationConstraint="$REGION"

aws s3api put-bucket-versioning \
  --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled

aws s3api put-bucket-encryption \
  --bucket "$BUCKET" \
  --server-side-encryption-configuration '{
    "Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]
  }'

aws s3api put-public-access-block \
  --bucket "$BUCKET" \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

echo "Creating DynamoDB lock table: $TABLE"
aws dynamodb create-table \
  --table-name "$TABLE" \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region "$REGION"

echo ""
echo "Bootstrap complete. Now run:"
echo "  cd infrastructure"
echo "  terraform init"
echo "  cp terraform.tfvars.example terraform.tfvars  # fill in your values"
echo "  terraform plan"
echo "  terraform apply"
