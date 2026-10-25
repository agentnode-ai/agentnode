# The vendored Ed25519 verifier against RFC 8032 section 7.1 (TEST 1, 2, 3, SHA(abc)) and against mutations of each
# (a flipped message bit, signature bit and key bit, S + L, non-canonical y): every valid vector must verify, every
# mutation must not. Run with Windows PowerShell 5.1:  powershell -NoProfile -File ed25519-vectors.ps1
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\Ed25519.ps1')
function hex([string]$s) { if ($s.Length -eq 0) { return ,(New-Object byte[] 0) }; $b = New-Object byte[] ($s.Length / 2); for ($i = 0; $i -lt $b.Length; $i++) { $b[$i] = [Convert]::ToByte($s.Substring($i * 2, 2), 16) }; return ,$b }
$vectors = @(
    @{ n = 'TEST 1'; pk = 'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a'; m = '';
       sig = 'e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b' },
    @{ n = 'TEST 2'; pk = '3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c'; m = '72';
       sig = '92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00' },
    @{ n = 'TEST 3'; pk = 'fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025'; m = 'af82';
       sig = '6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a' },
    @{ n = 'TEST SHA(abc)'; pk = 'ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf';
       m = 'ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f';
       sig = 'dc2a4459e7369633a52b1bf277839a00201009a3efbf3ecb69bea2186c26b58909351fc9ac90b3ecfdfbc7c66431e0303dca179c138ac17ad9bef1177331a704' }
)
$fail = 0
foreach ($v in $vectors) {
    $pk = hex $v.pk; $m = hex $v.m; $sig = hex $v.sig
    $ok = Test-Ed25519Signature -PublicKey $pk -Message $m -Signature $sig
    if (-not $ok) { Write-Output "FAIL $($v.n): valid signature refused"; $fail++ } else { Write-Output "ok   $($v.n) verifies" }
    $m2 = New-Object byte[] ([Math]::Max(1, $m.Length)); [Array]::Copy($m, $m2, $m.Length); $m2[0] = $m2[0] -bxor 1
    $s2 = [byte[]]$sig.Clone(); $s2[5] = $s2[5] -bxor 1
    $s3 = [byte[]]$sig.Clone(); $s3[40] = $s3[40] -bxor 1
    $k2 = [byte[]]$pk.Clone(); $k2[3] = $k2[3] -bxor 1
    foreach ($mut in @(@('message', $pk, $m2, $sig), @('R', $pk, $m, $s2), @('S', $pk, $m, $s3), @('key', $k2, $m, $sig))) {
        if (Test-Ed25519Signature -PublicKey $mut[1] -Message $mut[2] -Signature $mut[3]) { Write-Output "FAIL $($v.n): mutated $($mut[0]) accepted"; $fail++ }
    }
}
# S >= L must be refused even when [S]B would still match (malleability)
$v = $vectors[0]; $sig = hex $v.sig
$S = Ed-FromLE ([byte[]]$sig[32..63]) ; $S2 = $S + $script:EdL
$S2b = $S2.ToByteArray(); $fixed = New-Object byte[] 32; [Array]::Copy($S2b, $fixed, [Math]::Min(32, $S2b.Length))
$mall = [byte[]]$sig.Clone(); [Array]::Copy($fixed, 0, $mall, 32, 32)
if (Test-Ed25519Signature -PublicKey (hex $v.pk) -Message (hex $v.m) -Signature $mall) { Write-Output 'FAIL S+L accepted'; $fail++ } else { Write-Output 'ok   S + L refused' }
# a non-canonical public key (y >= p) must be refused
$nc = hex 'edffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f'
if (Test-Ed25519Signature -PublicKey $nc -Message (hex '') -Signature (hex $v.sig)) { Write-Output 'FAIL non-canonical key accepted'; $fail++ } else { Write-Output 'ok   non-canonical key refused' }
if ($fail -eq 0) { Write-Output 'ALL ED25519 VECTORS PASS'; exit 0 } else { Write-Output "$fail FAILURE(S)"; exit 1 }
