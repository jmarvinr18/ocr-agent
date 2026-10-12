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
            Resource = "arn:aws:s3:::${var.s3_bucket}/*"
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

# Public HTTPS endpoint for the upload UI. Requests are checked against
# UPLOAD_API_KEY inside the function (x-api-key header) when it is set.
resource "aws_lambda_function_url" "this" {
  function_name      = module.function.function_name
  authorization_type = "NONE"

  cors {
    allow_origins = var.ui_allowed_origins
    allow_methods = ["POST"]
    allow_headers = ["content-type", "x-api-key"]
    max_age       = 3600
  }
}

resource "aws_lambda_permission" "function_url" {
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = module.function.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "function_url_invoke" {
  statement_id             = "AllowPublicInvokeViaFunctionUrl"
  action                   = "lambda:InvokeFunction"
  function_name            = module.function.function_name
  principal                = "*"
  invoked_via_function_url = true
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
