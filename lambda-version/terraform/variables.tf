variable "region" {
  type    = string
  default = "ap-southeast-1"
}

variable "client" {
  type = string
}

variable "client_account_id" {
  type = string
}

variable "environment" {
  type = string
}

variable "function_name" {
  type = string
}

variable "description" {
  type    = string
  default = ""
}

variable "role_name" {
  type = string
}

variable "handler" {
  type    = string
  default = "lambda_function.lambda_handler"
}

variable "runtime" {
  type    = string
  default = "python3.12"
}

variable "timeout" {
  type    = number
  default = 30
}

variable "memory_size" {
  type    = number
  default = 128
}

variable "s3_bucket" {
  type        = string
  description = "S3 bucket the function is allowed to read/write."
}

variable "output_prefix" {
  type        = string
  default     = "markdown/"
  description = "Key prefix in s3_bucket where the generated .md files are written."
}

variable "upload_prefix" {
  type        = string
  default     = "uploads/"
  description = "Key prefix in s3_bucket where documents uploaded from the UI are stored."
}

variable "upload_api_key" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Access key the UI must send (x-api-key) to call the API. Empty disables the check."
}

variable "ui_allowed_origins" {
  type        = list(string)
  default     = ["*"]
  description = "Origins (the site hosting the UI, e.g. https://docs.example.com) allowed to upload to the bucket from a browser."
}

variable "alb_listener_arn" {
  type        = string
  description = "ARN of the existing ALB listener (usually HTTPS :443) that serves the site."
}

variable "alb_rule_priority" {
  type        = number
  description = "Priority for the new listener rule. Must be unused on that listener and lower than any catch-all rule."
}

variable "alb_path_pattern" {
  type        = string
  default     = "/api/document-processor"
  description = "Path on the site that routes to this Lambda. The UI calls this path."
}

variable "alb_host_headers" {
  type        = list(string)
  default     = []
  description = "Optional host names to match as well, if the listener serves several sites."
}

variable "alb_target_group_name" {
  type        = string
  default     = "document-processor-api"
  description = "Name of the Lambda target group (max 32 characters)."
}

variable "manage_bucket_cors" {
  type        = bool
  default     = true
  description = "Manage the bucket's CORS rules for browser uploads. Replaces any existing CORS rules."
}

variable "environment_variables" {
  type    = map(string)
  default = {}
}

variable "tags" {
  type    = map(string)
  default = {}
}
