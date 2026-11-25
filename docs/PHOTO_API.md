# Claim photographs and damage analysis

## Run the example

The example creates one synthetic claimant and a temporary SQLite database. It uploads an image, submits it for analysis, reads the stored result, and checks that the claim facts remain unchanged.

```powershell
uv sync --locked --group agents
uv run --locked --group agents python -m examples.photo_analysis
uv run --locked --group agents python -m examples.photo_analysis --provider groq --photo 'path/to/non-sensitive-photo.jpg'
uv run --locked --group agents pytest -q
```

The default uses a generated synthetic wall scene. A second generated fixture represents an irrelevant image. Unknown offline inputs return an insufficient-evidence result. The API labels fixture output `offline_fixture`. The optional Groq run uses `GROQ_API_KEY` from the environment or ignored local `.env`. It sends the normalized image and limited report context to Groq. The live verification source was a cropped public-domain FEMA photograph by Patsy Lynch, image 17647. [Photo and attribution](https://commons.wikimedia.org/wiki/File:FEMA_-_17647_-_Photograph_by_Patsy_Lynch_taken_on_10-18-2005_in_Louisiana.jpg)

Groq documents JSON mode for `qwen/qwen3.8-27b`, up to three images, 2,048 input tokens per image, and a 20 MB limit for requests containing an image URL. Groq lists the model on its free plan. Account and token limits still apply to a batch. This application caps each normalized JPEG at 750 KiB before Base64 encoding. [Groq vision](https://console.groq.com/docs/vision) · [Groq rate limits](https://console.groq.com/docs/rate-limits)

## Exercise the REST API

Start the app with a local claimant credential using [the intake API setup](INTAKE_API.md). Run the following in the client terminal and use a non-sensitive image. These commands use `curl.exe` for multipart upload and PowerShell for the JSON requests.

```powershell
$base = 'http://127.0.0.1:8000/api/v1'
$secret = Read-Host 'Local synthetic bearer token' -AsSecureString
$token = [Net.NetworkCredential]::new('', $secret).Password
$auth = @{ Authorization = "Bearer $token" }
$start = Invoke-RestMethod -Method Post -Uri "$base/conversations" -Headers (
    $auth + @{ 'Idempotency-Key' = [guid]::NewGuid().ToString() }
)
$claim = "$base/claims/$($start.claim.claim_id)"
$path = 'path/to/non-sensitive-photo.jpg'
$uploadKey = [guid]::NewGuid().ToString()
$photo = curl.exe --fail-with-body --silent --show-error `
    -H "Authorization: Bearer $token" -H "Idempotency-Key: $uploadKey" `
    -F "photo=@$path" "$claim/photos" | ConvertFrom-Json
$analysisKey = [guid]::NewGuid().ToString()
$request = @{ photo_ids = @($photo.id); context = 'The customer reports wall staining.' } | ConvertTo-Json
$analysis = Invoke-RestMethod -Method Post -Uri "$claim/photo-analyses" -Headers (
    $auth + @{ 'Idempotency-Key' = $analysisKey }
) -ContentType 'application/json' -Body $request
Invoke-RestMethod -Uri "$claim/photos" -Headers $auth
Invoke-WebRequest -Uri "$claim/photos/$($photo.id)/content" -Headers $auth -OutFile 'data/runtime/retrieved.jpg'
Invoke-RestMethod -Uri "$claim/photo-analyses/$($analysis.id)" -Headers $auth
Invoke-RestMethod -Uri $claim -Headers $auth
Invoke-RestMethod -Method Delete -Uri "$claim/photos/$($photo.id)" -Headers (
    $auth + @{ 'Idempotency-Key' = [guid]::NewGuid().ToString() }
)
Invoke-RestMethod -Uri "$claim/photo-analyses/$($analysis.id)" -Headers $auth
```

The last response has status `invalidated` and a null result. Both file retrieval and analysis are scoped through workspace, claimant and claim. An analysis error returns HTTP 503 with a saved analysis ID and a stable error code. The photo remains available. Retry with the same analysis key and payload. The analysis keeps its ID and increments attempts after a failed run. If a photo was deleted, a replay returns the invalidated state with HTTP 409. Identical concurrent requests may make separate model calls before one result commits.

The database keeps only sanitized JPEG bytes under generated IDs. Uploads accept JPEG, PNG and WebP, verified by decoding rather than by the client filename or declared MIME type. Defaults are 8 MiB per upload, 6,000 pixels per source edge, 16 million source pixels, a 1,024-pixel normalized edge and 30 active photos per claim. See `.env.example` for the configuration keys. Deletion removes the stored image bytes and dependent report contents. It also suppresses those observations from future intake replies. Account for any external backups under the operator's retention policy.
