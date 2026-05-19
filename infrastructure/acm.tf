# Domain + ACM certificate setup — uncomment when soko-ug.com NS propagation is confirmed.
# To re-enable: set domain_name = "soko-ug.com" in terraform.tfvars and uncomment below.

# resource "aws_acm_certificate" "frontend" {
#   count    = var.domain_name != "" ? 1 : 0
#   provider = aws.us_east_1
#
#   domain_name               = "app.${var.domain_name}"
#   subject_alternative_names = ["${var.domain_name}", "www.${var.domain_name}"]
#   validation_method         = "DNS"
#
#   lifecycle {
#     create_before_destroy = true
#   }
#
#   tags = { Name = "soko-frontend-cert" }
# }
#
# resource "aws_route53_zone" "main" {
#   count = var.domain_name != "" ? 1 : 0
#   name  = var.domain_name
#   tags  = { Name = "soko-zone" }
# }
#
# resource "aws_route53_record" "cert_validation" {
#   for_each = var.domain_name != "" ? {
#     for dvo in aws_acm_certificate.frontend[0].domain_validation_options : dvo.domain_name => {
#       name   = dvo.resource_record_name
#       type   = dvo.resource_record_type
#       record = dvo.resource_record_value
#     }
#   } : {}
#
#   zone_id = aws_route53_zone.main[0].zone_id
#   name    = each.value.name
#   type    = each.value.type
#   ttl     = 60
#   records = [each.value.record]
# }
#
# resource "aws_acm_certificate_validation" "frontend" {
#   count                   = var.domain_name != "" ? 1 : 0
#   provider                = aws.us_east_1
#   certificate_arn         = aws_acm_certificate.frontend[0].arn
#   validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
# }
#
# resource "aws_route53_record" "root" {
#   count   = var.domain_name != "" ? 1 : 0
#   zone_id = aws_route53_zone.main[0].zone_id
#   name    = var.domain_name
#   type    = "A"
#   ttl     = 300
#   records = [aws_eip.soko.public_ip]
# }
#
# resource "aws_route53_record" "www" {
#   count   = var.domain_name != "" ? 1 : 0
#   zone_id = aws_route53_zone.main[0].zone_id
#   name    = "www.${var.domain_name}"
#   type    = "A"
#   ttl     = 300
#   records = [aws_eip.soko.public_ip]
# }
#
# resource "aws_route53_record" "app" {
#   count   = var.domain_name != "" ? 1 : 0
#   zone_id = aws_route53_zone.main[0].zone_id
#   name    = "app.${var.domain_name}"
#   type    = "CNAME"
#   ttl     = 300
#   records = [aws_cloudfront_distribution.frontend.domain_name]
# }
#
# output "route53_nameservers" {
#   description = "Point your domain registrar to these nameservers"
#   value       = var.domain_name != "" ? aws_route53_zone.main[0].name_servers : []
# }
#
# output "acm_validation_records" {
#   description = "Add these DNS records to validate your ACM certificate"
#   value = var.domain_name != "" ? {
#     for dvo in aws_acm_certificate.frontend[0].domain_validation_options : dvo.domain_name => {
#       name  = dvo.resource_record_name
#       type  = dvo.resource_record_type
#       value = dvo.resource_record_value
#     }
#   } : {}
# }
