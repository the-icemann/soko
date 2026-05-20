# Origin Access Control — lets CloudFront fetch from private S3
resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "soko-frontend-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

locals {
  # Path prefixes handled by NGINX on EC2 — must match nginx.conf location blocks exactly
  api_path_prefixes = [
    "/auth/*", "/oauth/*", "/users/*", "/listings/*", "/orders/*",
    "/payments/*", "/webhook/*", "/message/*", "/notifications/*",
    "/posts/*", "/ussd/*", "/recommendations/*", "/ml/*", "/health*"
  ]
}

resource "aws_cloudfront_distribution" "frontend" {
  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  price_class         = "PriceClass_200" # includes South Africa edge nodes

  aliases = var.domain_name != "" ? ["app.${var.domain_name}"] : []

  # depends_on = [aws_acm_certificate_validation.frontend]  # uncomment with domain

  # S3 origin — serves static frontend assets
  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "s3-frontend"
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  # EC2 origin — CloudFront talks HTTP to EC2 so the browser always uses HTTPS.
  # nip.io maps "<ip>.nip.io" → that IP, giving CloudFront a valid hostname.
  origin {
    domain_name = "${aws_eip.soko.public_ip}.nip.io"
    origin_id   = "ec2-api"
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "s3-frontend"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }

    min_ttl     = 0
    default_ttl = 86400   # 1 day for HTML
    max_ttl     = 31536000
  }

  # SPA auth pages — these share the /auth/ prefix with backend API routes.
  # List them explicitly BEFORE the /auth/* EC2 behavior so CloudFront serves
  # them from S3 instead of proxying to NGINX. Deep-links and page-reloads work
  # because S3 returns 404→index.html via the custom_error_response below.
  ordered_cache_behavior {
    path_pattern           = "/auth/sign-in"
    target_origin_id       = "s3-frontend"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }
    min_ttl     = 0
    default_ttl = 0
    max_ttl     = 0
  }

  ordered_cache_behavior {
    path_pattern           = "/auth/sign-up"
    target_origin_id       = "s3-frontend"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }
    min_ttl     = 0
    default_ttl = 0
    max_ttl     = 0
  }

  # complete-profile: forward query_string so the ?access_token= param (set by
  # the backend after Google OAuth for returning users) reaches the SPA.
  ordered_cache_behavior {
    path_pattern           = "/auth/complete-profile"
    target_origin_id       = "s3-frontend"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    forwarded_values {
      query_string = true
      cookies { forward = "none" }
    }
    min_ttl     = 0
    default_ttl = 0
    max_ttl     = 0
  }

  # Cache JS/CSS/images aggressively (content-hashed filenames by Vite)
  ordered_cache_behavior {
    path_pattern           = "/assets/*"
    target_origin_id       = "s3-frontend"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }

    min_ttl     = 0
    default_ttl = 31536000  # 1 year — Vite hashes filenames on every build
    max_ttl     = 31536000
  }

  # API behaviors — route each service path prefix through to EC2, no caching
  dynamic "ordered_cache_behavior" {
    for_each = local.api_path_prefixes
    content {
      path_pattern           = ordered_cache_behavior.value
      target_origin_id       = "ec2-api"
      viewer_protocol_policy = "redirect-to-https"
      allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "PATCH", "POST", "DELETE"]
      cached_methods         = ["GET", "HEAD"]
      compress               = false

      forwarded_values {
        query_string = true
        headers      = ["Authorization", "Content-Type", "Accept", "Origin", "X-User-Id", "X-User-Role"]
        cookies { forward = "all" }
      }

      min_ttl     = 0
      default_ttl = 0
      max_ttl     = 0
    }
  }

  # SPA routing: 404 from S3 → serve index.html so React Router handles it
  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }

  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
    # Uncomment below and remove line above when domain is ready:
    # acm_certificate_arn            = aws_acm_certificate_validation.frontend[0].certificate_arn
    # ssl_support_method             = "sni-only"
    # minimum_protocol_version       = "TLSv1.2_2021"
    # cloudfront_default_certificate = false
  }

  tags = { Name = "soko-frontend-cdn" }
}
