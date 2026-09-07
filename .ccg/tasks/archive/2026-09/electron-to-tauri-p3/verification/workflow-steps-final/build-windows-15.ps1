if ($env:CHAOXING_PFX_BASE64) {
  $certificateFile = Join-Path $env:RUNNER_TEMP "chaoxing-sign-$([Guid]::NewGuid().ToString('N')).pfx"
  try {
    [IO.File]::WriteAllBytes($certificateFile, [Convert]::FromBase64String($env:CHAOXING_PFX_BASE64))
    $password = ConvertTo-SecureString $env:CHAOXING_PFX_PASSWORD -AsPlainText -Force
    $importedCertificates = @(Import-PfxCertificate -FilePath $certificateFile -CertStoreLocation Cert:\CurrentUser\My -Password $password)
    $signingCertificates = @($importedCertificates | Where-Object HasPrivateKey)
    if ($signingCertificates.Count -ne 1) { throw 'PFX must identify exactly one signing certificate with a private key' }
    $env:CHAOXING_SIGN_CERT_THUMBPRINT = $signingCertificates[0].Thumbprint
    "CHAOXING_SIGN_CERT_THUMBPRINT=$env:CHAOXING_SIGN_CERT_THUMBPRINT" >> $env:GITHUB_ENV
  } finally {
    if (Test-Path -LiteralPath $certificateFile) { Remove-Item -LiteralPath $certificateFile -Force }
  }
}
pwsh -NoProfile -File desktop/scripts/sign-windows.ps1 -Mode Inspect -ReportPath desktop/release/verification/signing-availability.json
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
