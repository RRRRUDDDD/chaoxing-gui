if ($env:CHAOXING_PFX_BASE64) {
  $certificateFile = Join-Path $env:RUNNER_TEMP "chaoxing-sign-$([Guid]::NewGuid().ToString('N')).pfx"
  try {
    [IO.File]::WriteAllBytes($certificateFile, [Convert]::FromBase64String($env:CHAOXING_PFX_BASE64))
    $password = ConvertTo-SecureString $env:CHAOXING_PFX_PASSWORD -AsPlainText -Force
    $certificate = Import-PfxCertificate -FilePath $certificateFile -CertStoreLocation Cert:\CurrentUser\My -Password $password
    if (-not $certificate.HasPrivateKey) { throw 'Imported signing certificate has no private key' }
    "CHAOXING_SIGN_CERT_THUMBPRINT=$($certificate.Thumbprint)" >> $env:GITHUB_ENV
  } finally {
    if (Test-Path -LiteralPath $certificateFile) { Remove-Item -LiteralPath $certificateFile -Force }
  }
}
pwsh -NoProfile -File desktop/scripts/sign-windows.ps1 -Mode Inspect -ReportPath desktop/release/verification/signing-availability.json
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
