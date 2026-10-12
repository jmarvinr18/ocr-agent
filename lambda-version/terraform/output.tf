output "function_arn" {
  value = module.function.arn
}

output "function_name" {
  value = module.function.function_name
}

output "invoke_arn" {
  value = module.function.invoke_arn
}

output "role_arn" {
  value = module.role.arn
}

output "upload_api_target_group_arn" {
  value = aws_lb_target_group.upload_api.arn
}
