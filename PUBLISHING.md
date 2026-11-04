# Publishing

The repository is prepared for `https://github.com/xJaike/omarchy-drive`.
No repository or marketplace listing exists until these steps succeed.

1. Check GitHub access in a normal terminal with `gh auth status`.
   If needed, run `gh auth login --hostname github.com`.
2. From this directory, publish the prepared commit:

   ```bash
   gh repo create xJaike/omarchy-drive --public --source=. --push \
     --description 'Google Drive offline sync status and live transfer progress for Omarchy'
   ```

3. Open the [official submission form](https://github.com/omacom/omarchy-plugin-marketplace/issues/new?template=submit-plugin.yml)
   and provide the public repository URL, a description, category, and tags.
   Use the text in `MARKETPLACE.md` as the draft.

The marketplace runs automated validation and requires maintainer approval.
This is independent of publishing the GitHub repository.
See the [publishing guide](https://plugins.omarchy.org/publish.html).

Before submitting, verify the plugin loads in your running shell and test its
click actions. QML syntax and automated tests do not replace a desktop check.
