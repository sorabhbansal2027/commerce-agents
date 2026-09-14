#!/bin/bash
# Packages LWC components into a Salesforce metadata zip for Workbench deploy.
# Usage: bash scripts/package_lwc.sh
# Then upload commerce-lwc.zip at:
#   https://workbench.developerforce.com → Deploy → Metadata → choose zip → Next

set -e
cd "$(dirname "$0")/.."

TMPDIR=$(mktemp -d)

for COMPONENT in commerceAgentPanel salesforceMerchant; do
    mkdir -p "$TMPDIR/MyPackage/lwc/$COMPONENT"
    for f in js html css js-meta.xml; do
        cp "embed/lwc/$COMPONENT/$COMPONENT.$f" \
           "$TMPDIR/MyPackage/lwc/$COMPONENT/"
    done
done

cat > "$TMPDIR/MyPackage/package.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<Package xmlns="http://soap.sforce.com/2006/04/metadata">
    <types>
        <members>commerceAgentPanel</members>
        <members>salesforceMerchant</members>
        <name>LightningComponentBundle</name>
    </types>
    <version>62.0</version>
</Package>
EOF

(cd "$TMPDIR" && zip -r commerce-lwc.zip MyPackage/)
cp "$TMPDIR/commerce-lwc.zip" .
rm -rf "$TMPDIR"

echo "Created: commerce-lwc.zip"
echo ""
echo "Deploy steps:"
echo "  1. Go to https://workbench.developerforce.com"
echo "  2. Login with your Salesforce sandbox credentials"
echo "  3. Deploy → Metadata"
echo "  4. Choose file → select commerce-lwc.zip"
echo "  5. Click Next → Deploy"
