data "archive_file" "this" {
  type        = "zip"
  source_dir  = "${path.root}/src"
  output_path = "${path.root}/.build/${var.function_name}.zip"
}

# Third-party packages ship as a layer. Wheels are fetched for the Lambda
# platform (Linux, CPython 3.12) so the build works from Windows too.
resource "terraform_data" "deps" {
  triggers_replace = filesha256("${path.root}/requirements.txt")

  provisioner "local-exec" {
    command = "python -c \"import shutil; shutil.rmtree('${path.root}/.build/layer', ignore_errors=True)\" && python -m pip install -r \"${path.root}/requirements.txt\" --target \"${path.root}/.build/layer/python\" --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 --only-binary=:all: --upgrade"
  }
}

data "archive_file" "deps" {
  type        = "zip"
  source_dir  = "${path.root}/.build/layer"
  output_path = "${path.root}/.build/${var.function_name}-deps.zip"

  depends_on = [terraform_data.deps]
}

resource "aws_lambda_layer_version" "deps" {
  layer_name          = "${var.function_name}-deps"
  filename            = data.archive_file.deps.output_path
  source_code_hash    = data.archive_file.deps.output_base64sha256
  compatible_runtimes = [var.runtime]
}

module "role" {
  source = "git::ssh://git@github.com/jmarvinr18/infra-as-code.git//terraform/provider/aws/modules/lambda/role"

  role_name = var.role_name

  managed_policy_arns = [
    "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
  ]

  inline_policies = [
    {
      name = "s3-read-write"
      policy = jsonencode({
        Version = "2012-10-17"
        Statement = [
          {
            Effect = "Allow"
            Action = [

              "s3:GetObject",
              "s3:PutObject",
              "s3:DeleteObject",
              "s3:ListBucket"
            ]
            Resource = [
              "arn:aws:s3:::${var.s3_bucket}",
              "arn:aws:s3:::${var.s3_bucket}/*"
            ]
          }
        ]
      })
    },
    {
      # Upload jobs run as an async invocation of this same function.
      name = "self-invoke"
      policy = jsonencode({
        Version = "2012-10-17"
        Statement = [
          {
            Effect   = "Allow"
            Action   = ["lambda:InvokeFunction"]
            Resource = "arn:aws:lambda:${var.region}:${var.client_account_id}:function:${var.function_name}"
          }
        ]
      })
    },
    {
      name = "ai-service-read"
      policy = jsonencode({
        Version = "2012-10-17"
        Statement = [
          {
            Sid    = "TextractRead"
            Effect = "Allow"
            Action = [
              "textract:GetDocumentTextDetection",
              "textract:GetDocumentAnalysis"
            ]
            Resource = "*"
          },
          {
            Sid    = "RekognitionRead"
            Effect = "Allow"
            Action = [
              "rekognition:GetLabelDetection",
              "rekognition:DetectLabels"
            ]
            Resource = "*"
          },
          {
            Sid    = "TranscribeRead"
            Effect = "Allow"
            Action = [
              "transcribe:GetTranscriptionJob"
            ]
            Resource = "*"
          },
          {
            Sid    = "ComprehendDetect"
            Effect = "Allow"
            Action = [
              "comprehend:DetectEntities",
              "comprehend:DetectKeyPhrases",
              "comprehend:DetectSentiment",
              "comprehend:DetectDominantLanguage",
              "comprehend:DetectPiiEntities"
            ]
            Resource = "*"
          }
        ]
      })
    },
    {
      name = "bedrock-invoke"
      policy = jsonencode({
        Version = "2012-10-17"
        Statement = [
          {
            Sid    = "BedrockInvoke"
            Effect = "Allow"
            Action = [
              "bedrock:InvokeModel",
              "bedrock:InvokeModelWithResponseStream"
            ]
            Resource = [
              "arn:aws:bedrock:*::foundation-model/*",
              "arn:aws:bedrock:*:${var.client_account_id}:inference-profile/*"
            ]
          }
        ]
      })
    }
  ]

  tags = var.tags
}

module "function" {
  source = "git::ssh://git@github.com/jmarvinr18/infra-as-code.git//terraform/provider/aws/modules/lambda/function"

  function_name    = var.function_name
  description      = var.description
  role_arn         = module.role.arn
  handler          = var.handler
  runtime          = var.runtime
  filename         = data.archive_file.this.output_path
  source_code_hash = data.archive_file.this.output_base64sha256
  layers           = [aws_lambda_layer_version.deps.arn]
  timeout          = var.timeout
  memory_size      = var.memory_size

  environment_variables = merge(var.environment_variables, {
    OUTPUT_BUCKET  = var.s3_bucket
    OUTPUT_PREFIX  = var.output_prefix
    UPLOAD_PREFIX  = var.upload_prefix
    UPLOAD_API_KEY = var.upload_api_key
  })

  tags = var.tags
}

# Async upload jobs record their own failures in status.json, so a Lambda
# retry would only repeat a slow, paid conversion.
resource "aws_lambda_function_event_invoke_config" "this" {
  function_name          = module.function.function_name
  maximum_retry_attempts = 0
}

# --- Upload UI API: existing ALB -> Lambda target group ----------------------
# Requests are checked against UPLOAD_API_KEY inside the function (x-api-key
# header) when it is set.

resource "aws_lb_target_group" "upload_api" {
  name        = var.alb_target_group_name
  target_type = "lambda"
  tags        = var.tags
}

resource "aws_lambda_permission" "alb" {
  statement_id  = "AllowExecutionFromALB"
  action        = "lambda:InvokeFunction"
  function_name = module.function.function_name
  principal     = "elasticloadbalancing.amazonaws.com"
  source_arn    = aws_lb_target_group.upload_api.arn
}

resource "aws_lb_target_group_attachment" "upload_api" {
  target_group_arn = aws_lb_target_group.upload_api.arn
  target_id        = module.function.arn

  depends_on = [aws_lambda_permission.alb]
}

resource "aws_lb_listener_rule" "upload_api" {
  listener_arn = var.alb_listener_arn
  priority     = var.alb_rule_priority

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.upload_api.arn
  }

  condition {
    path_pattern {
      values = [var.alb_path_pattern]
    }
  }

  dynamic "condition" {
    for_each = length(var.alb_host_headers) > 0 ? [1] : []
    content {
      host_header {
        values = var.alb_host_headers
      }
    }
  }

  tags = var.tags
}

# The UI uploads straight to S3 with a presigned POST, which needs CORS on the bucket.
# NOTE: this replaces any CORS rules already on the bucket.
resource "aws_s3_bucket_cors_configuration" "uploads" {
  count  = var.manage_bucket_cors ? 1 : 0
  bucket = var.s3_bucket

  cors_rule {
    allowed_methods = ["POST"]
    allowed_origins = var.ui_allowed_origins
    allowed_headers = ["*"]
    max_age_seconds = 3600
  }
}
