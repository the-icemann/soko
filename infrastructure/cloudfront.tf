# Origin Access Control — lets CloudFront fetch from private S3
resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "soko-frontend-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "frontend" {
  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  price_class         = "PriceClass_200" # includes South Africa edge nodes

  aliases = var.domain_name != "" ? ["app.${var.domain_name}"] : []

  # depends_on = [aws_acm_certificate_validation.frontend]  # uncomment with domain

  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "s3-frontend"
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
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
