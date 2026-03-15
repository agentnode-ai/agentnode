import { Command } from "commander";
import { getPackage } from "../api.js";

export const infoCommand = new Command("info")
  .description("Show details about a package")
  .argument("<slug>", "Package slug")
  .action(async (slug: string) => {
    try {
      const pkg = await getPackage(slug);

      console.log(`\n  ${pkg.name} (${pkg.slug})`);
      console.log(`  ${pkg.summary}`);
      console.log(`  Type:       ${pkg.package_type}`);
      console.log(`  Downloads:  ${pkg.download_count}`);
      console.log(`  Deprecated: ${pkg.is_deprecated ? "Yes" : "No"}`);

      if (pkg.latest_version) {
        console.log(`  Latest:     ${pkg.latest_version.version_number} (${pkg.latest_version.channel})`);
      }

      if (pkg.publisher) {
        console.log(`  Publisher:  ${pkg.publisher.display_name} [${pkg.publisher.trust_level}]`);
      }

      if (pkg.description) {
        console.log(`\n  ${pkg.description}`);
      }

      if (pkg.blocks?.capabilities?.length > 0) {
        console.log(`\n  Capabilities:`);
        for (const cap of pkg.blocks.capabilities) {
          console.log(`    - ${cap.name} (${cap.capability_id})`);
        }
      }

      if (pkg.blocks?.permissions) {
        const p = pkg.blocks.permissions;
        console.log(`\n  Permissions:`);
        console.log(`    network: ${p.network_level}, filesystem: ${p.filesystem_level}`);
        console.log(`    code_execution: ${p.code_execution_level}, data_access: ${p.data_access_level}`);
      }

      console.log();
    } catch (err: any) {
      console.error(`Error: ${err.message}`);
      process.exit(1);
    }
  });
