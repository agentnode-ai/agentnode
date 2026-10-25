# Ed25519 signature VERIFICATION (RFC 8032, section 5.1.7) for Windows PowerShell 5.1 -- local-worker slice 2,
# Codex Q-LW2-0001 D7 as amended: "Windows MUST use a repository-vendored, test-vector-covered Ed25519 verifier".
# .NET Framework 4.8 has no Ed25519, so this is the reference algorithm on System.Numerics.BigInteger: decode A and
# R (rejecting non-canonical y >= p), reject S >= L, h = SHA-512(R || A || M) mod L, and accept only when
# [S]B == R + [h]A. Verification only: there is no secret here, so timing does not matter. Covered by the RFC 8032
# test vectors in tests\ed25519-vectors.ps1.
Add-Type -AssemblyName System.Numerics

$script:EdP = [System.Numerics.BigInteger]::Pow(2, 255) - 19
$script:EdL = [System.Numerics.BigInteger]::Pow(2, 252) + [System.Numerics.BigInteger]::Parse('27742317777372353535851937790883648493')

function Ed-Mod([System.Numerics.BigInteger]$x) {
    $r = [System.Numerics.BigInteger]::Remainder($x, $script:EdP)
    if ($r.Sign -lt 0) { $r = $r + $script:EdP }
    return $r
}
function Ed-Inv([System.Numerics.BigInteger]$x) {
    return [System.Numerics.BigInteger]::ModPow((Ed-Mod $x), $script:EdP - 2, $script:EdP)
}
$script:EdD = Ed-Mod ((-121665) * (Ed-Inv 121666))
$script:EdI = [System.Numerics.BigInteger]::ModPow(2, ($script:EdP - 1) / 4, $script:EdP)

function Ed-FromLE([byte[]]$bytes) {
    $copy = New-Object byte[] ($bytes.Length + 1)              # a trailing zero byte: always non-negative
    [Array]::Copy($bytes, $copy, $bytes.Length)
    return New-Object System.Numerics.BigInteger (, $copy)
}

function Ed-RecoverX([System.Numerics.BigInteger]$y, [int]$sign) {
    if ($y -ge $script:EdP) { return $null }
    $y2 = Ed-Mod ($y * $y)
    $x2 = Ed-Mod (($y2 - 1) * (Ed-Inv ($script:EdD * $y2 + 1)))
    if ($x2.IsZero) { if ($sign -ne 0) { return $null }; return [System.Numerics.BigInteger]::Zero }
    $x = [System.Numerics.BigInteger]::ModPow($x2, ($script:EdP + 3) / 8, $script:EdP)
    if (-not (Ed-Mod ($x * $x - $x2)).IsZero) { $x = Ed-Mod ($x * $script:EdI) }
    if (-not (Ed-Mod ($x * $x - $x2)).IsZero) { return $null }
    if ([int]($x % 2) -ne $sign) { $x = $script:EdP - $x }
    return $x
}

function Ed-Decode([byte[]]$s) {
    if ($s.Length -ne 32) { return $null }
    $b = [byte[]]$s.Clone()
    $sign = ($b[31] -shr 7) -band 1
    $b[31] = $b[31] -band 0x7f
    $y = Ed-FromLE $b
    $x = Ed-RecoverX $y $sign
    if ($null -eq $x) { return $null }
    return ,@($x, $y, [System.Numerics.BigInteger]::One, (Ed-Mod ($x * $y)))
}

function Ed-Add($P, $Q) {
    $A = Ed-Mod (($P[1] - $P[0]) * ($Q[1] - $Q[0]))
    $B = Ed-Mod (($P[1] + $P[0]) * ($Q[1] + $Q[0]))
    $C = Ed-Mod ($P[3] * 2 * $script:EdD * $Q[3])
    $D = Ed-Mod ($P[2] * 2 * $Q[2])
    $E = $B - $A; $F = $D - $C; $G = $D + $C; $H = $B + $A
    return ,@((Ed-Mod ($E * $F)), (Ed-Mod ($G * $H)), (Ed-Mod ($F * $G)), (Ed-Mod ($E * $H)))
}

function Ed-Mul([System.Numerics.BigInteger]$s, $P) {
    $Q = @([System.Numerics.BigInteger]::Zero, [System.Numerics.BigInteger]::One, [System.Numerics.BigInteger]::One, [System.Numerics.BigInteger]::Zero)
    while ($s.Sign -gt 0) {
        if (-not $s.IsEven) { $Q = Ed-Add $Q $P }
        $P = Ed-Add $P $P
        $s = $s / 2
    }
    return ,$Q
}

function Ed-Equal($P, $Q) {
    if (-not (Ed-Mod ($P[0] * $Q[2] - $Q[0] * $P[2])).IsZero) { return $false }
    return (Ed-Mod ($P[1] * $Q[2] - $Q[1] * $P[2])).IsZero
}

$script:EdBy = Ed-Mod (4 * (Ed-Inv 5))
$script:EdBx = Ed-RecoverX $script:EdBy 0
$script:EdB = @($script:EdBx, $script:EdBy, [System.Numerics.BigInteger]::One, (Ed-Mod ($script:EdBx * $script:EdBy)))

function Test-Ed25519Signature {
    param([byte[]]$PublicKey, [byte[]]$Message, [byte[]]$Signature)
    if ($null -eq $PublicKey -or $null -eq $Signature -or $PublicKey.Length -ne 32 -or $Signature.Length -ne 64) { return $false }
    if ($null -eq $Message) { $Message = New-Object byte[] 0 }
    $A = Ed-Decode $PublicKey
    if ($null -eq $A) { return $false }
    $Rs = New-Object byte[] 32; [Array]::Copy($Signature, 0, $Rs, 0, 32)
    $Ss = New-Object byte[] 32; [Array]::Copy($Signature, 32, $Ss, 0, 32)
    $R = Ed-Decode $Rs
    if ($null -eq $R) { return $false }
    $S = Ed-FromLE $Ss
    if ($S -ge $script:EdL) { return $false }
    $sha = [System.Security.Cryptography.SHA512]::Create()
    $buf = New-Object byte[] (64 + $Message.Length)
    [Array]::Copy($Rs, 0, $buf, 0, 32); [Array]::Copy($PublicKey, 0, $buf, 32, 32)
    [Array]::Copy($Message, 0, $buf, 64, $Message.Length)
    $h = [System.Numerics.BigInteger]::Remainder((Ed-FromLE ($sha.ComputeHash($buf))), $script:EdL)
    $sB = Ed-Mul $S $script:EdB
    $hA = Ed-Mul $h $A
    return (Ed-Equal $sB (Ed-Add $R $hA))
}
