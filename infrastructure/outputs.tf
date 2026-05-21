output "ec2_public_ip" {
  description = "Elastic IP — point your domain A record here"
  value       = aws_eip.soko.public_ip
}

output "ec2_instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.soko.id
}

output "cloudfront_domain" {
  description = "CloudFront domain — use as CNAME for app.yourdomain.com"
  value       = aws_cloudfront_distribution.frontend.domain_name
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution ID — needed for cache invalidations in CI"
  value       = aws_cloudfront_distribution.frontend.id
}

output "frontend_bucket_name" {
  description = "S3 bucket name for frontend assets"
  value       = aws_s3_bucket.frontend.bucket
}

output "images_bucket_name" {
  description = "S3 bucket name for produce images"
  value       = aws_s3_bucket.images.bucket
}

output "platform_secret_arn" {
  description = "ARN of the platform secrets in Secrets Manager"
  value       = aws_secretsmanager_secret.platform.arn
}

output "ml_secret_arn" {
  description = "ARN of the ML secrets in Secrets Manager"
  value       = aws_secretsmanager_secret.ml.arn
}

output "ssh_command" {
  description = "SSH command to connect to the server"
  value       = "ssh -i ~/.ssh/${var.ec2_key_name}.pem ubuntu@${aws_eip.soko.public_ip}"
}
