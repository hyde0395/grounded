"""install_scan is pure logic — test it directly, no subprocess needed."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import install_scan  # noqa: E402


class PackageSpecsTest(unittest.TestCase):
    def specs(self, command):
        return install_scan.package_specs(command)

    def test_pip_install_basic(self):
        self.assertEqual(self.specs("pip install requests"), [("pypi", "requests")])

    def test_pip3_and_version_specifier(self):
        self.assertEqual(self.specs("pip3 install requests==2.31.0"), [("pypi", "requests")])

    def test_pip_extras_and_range(self):
        self.assertEqual(self.specs("pip install 'uvicorn[standard]>=0.29'"), [("pypi", "uvicorn")])

    def test_pip_requirements_file_skipped(self):
        self.assertEqual(self.specs("pip install -r requirements.txt"), [])

    def test_pip_local_and_url_skipped(self):
        self.assertEqual(self.specs("pip install ."), [])
        self.assertEqual(self.specs("pip install git+https://github.com/x/y"), [])
        self.assertEqual(self.specs("pip install ./pkg"), [])

    def test_python_dash_m_pip(self):
        self.assertEqual(self.specs("python3 -m pip install flask"), [("pypi", "flask")])

    def test_uv_add_and_uv_pip(self):
        self.assertEqual(self.specs("uv add httpx"), [("pypi", "httpx")])
        self.assertEqual(self.specs("uv pip install httpx"), [("pypi", "httpx")])

    def test_npm_install_with_version(self):
        self.assertEqual(self.specs("npm install lodash@4.17.21"), [("npm", "lodash")])

    def test_npm_scoped_package(self):
        self.assertEqual(self.specs("npm i @types/node@20"), [("npm", "@types/node")])

    def test_npm_flags_skipped(self):
        self.assertEqual(self.specs("npm install --save-dev typescript"), [("npm", "typescript")])

    def test_npm_bare_install_is_empty(self):
        self.assertEqual(self.specs("npm install"), [])

    def test_yarn_and_pnpm(self):
        self.assertEqual(self.specs("yarn add react"), [("npm", "react")])
        self.assertEqual(self.specs("pnpm add vue"), [("npm", "vue")])

    def test_cargo_add_and_install(self):
        self.assertEqual(self.specs("cargo add serde@1"), [("crates", "serde")])
        self.assertEqual(self.specs("cargo install ripgrep"), [("crates", "ripgrep")])

    def test_poetry_add_maps_to_pypi(self):
        self.assertEqual(self.specs("poetry add httpx"), [("pypi", "httpx")])

    def test_poetry_add_strips_at_constraint(self):
        # poetry uses name@constraint as well as name>=x
        self.assertEqual(self.specs("poetry add 'pendulum@^2.0.5'"), [("pypi", "pendulum")])

    def test_bun_add_maps_to_npm(self):
        self.assertEqual(self.specs("bun add react"), [("npm", "react")])
        self.assertEqual(self.specs("bun add lodash@4.17.21"), [("npm", "lodash")])
        self.assertEqual(self.specs("bun add @types/node"), [("npm", "@types/node")])

    def test_gem_install_maps_to_rubygems(self):
        self.assertEqual(self.specs("gem install rails"), [("rubygems", "rails")])

    def test_gem_install_version_flag_not_treated_as_name(self):
        # `-v 7.0`: the 7.0 is a version, not a gem
        self.assertEqual(self.specs("gem install rails -v 7.0"), [("rubygems", "rails")])

    def test_gem_install_multiple(self):
        self.assertEqual(self.specs("gem install rails rake"),
                         [("rubygems", "rails"), ("rubygems", "rake")])

    def test_bundle_add_maps_to_rubygems(self):
        self.assertEqual(self.specs("bundle add rails"), [("rubygems", "rails")])
        self.assertEqual(self.specs("bundle add rails --version '~> 7.0'"),
                         [("rubygems", "rails")])

    def test_composer_require_maps_to_packagist(self):
        self.assertEqual(self.specs("composer require monolog/monolog"),
                         [("packagist", "monolog/monolog")])

    def test_composer_require_strips_version_and_dev_flag(self):
        self.assertEqual(self.specs("composer require --dev 'phpunit/phpunit:^10.0'"),
                         [("packagist", "phpunit/phpunit")])

    def test_composer_php_extension_without_vendor_skipped(self):
        # `ext-gd` is a PHP extension, not a Packagist package
        self.assertEqual(self.specs("composer require ext-gd"), [])

    # A custom index/registry means the package lives on a registry grounded
    # cannot query — checking it against the public one would falsely STOP a
    # legitimate private install. Skip G-2 for the whole segment (fail open).
    def test_pip_index_url_skips_check(self):
        self.assertEqual(
            self.specs("pip install --index-url https://pypi.internal/simple acme-lib"), [])

    def test_pip_short_index_flag_skips_check(self):
        self.assertEqual(self.specs("pip install -i https://pypi.internal/simple acme-lib"), [])

    def test_pip_extra_index_url_skips_check(self):
        self.assertEqual(
            self.specs("pip install --extra-index-url https://pypi.internal acme-lib"), [])

    def test_uv_pip_index_url_skips_check(self):
        self.assertEqual(
            self.specs("uv pip install --index-url https://pypi.internal acme-lib"), [])

    def test_npm_custom_registry_skips_check(self):
        self.assertEqual(
            self.specs("npm install --registry=https://npm.internal @acme/lib"), [])

    def test_yarn_custom_registry_skips_check(self):
        self.assertEqual(
            self.specs("yarn add --registry https://npm.internal acme-lib"), [])

    def test_gem_custom_source_skips_check(self):
        self.assertEqual(
            self.specs("gem install acme-lib --source https://gems.internal"), [])

    def test_cargo_custom_registry_skips_check(self):
        self.assertEqual(self.specs("cargo add acme-lib --registry internal"), [])

    def test_public_install_still_checked(self):
        # the guard must not suppress ordinary public-registry installs
        self.assertEqual(self.specs("pip install requests"), [("pypi", "requests")])

    def test_sudo_and_env_prefix(self):
        self.assertEqual(self.specs("sudo pip install requests"), [("pypi", "requests")])
        self.assertEqual(self.specs("PIP_NO_CACHE=1 pip install requests"), [("pypi", "requests")])

    def test_multiple_packages_and_segments(self):
        self.assertEqual(
            self.specs("pip install flask requests && npm install lodash"),
            [("pypi", "flask"), ("pypi", "requests"), ("npm", "lodash")],
        )

    def test_dedup(self):
        self.assertEqual(self.specs("pip install x; pip install x"), [("pypi", "x")])

    def test_non_install_commands_empty(self):
        self.assertEqual(self.specs("pip freeze && npm run build && cargo test"), [])


class ManifestInstallsTest(unittest.TestCase):
    def mi(self, command):
        return install_scan.manifest_installs(command)

    def test_npm_install_bare(self):
        self.assertEqual(self.mi("npm install"), [("npm", "package.json")])
        self.assertEqual(self.mi("npm ci"), [("npm", "package.json")])

    def test_npm_install_with_name_is_not_manifest(self):
        self.assertEqual(self.mi("npm install lodash"), [])

    def test_pip_dash_r(self):
        self.assertEqual(self.mi("pip install -r requirements.txt"),
                         [("pypi", "requirements.txt")])

    def test_poetry_and_bundle_and_composer(self):
        self.assertEqual(self.mi("poetry install"), [("pypi", "pyproject.toml")])
        self.assertEqual(self.mi("bundle install"), [("rubygems", "Gemfile")])
        self.assertEqual(self.mi("composer install"),
                         [("packagist", "composer.json")])

    def test_cargo_build(self):
        self.assertEqual(self.mi("cargo build"), [("crates", "Cargo.toml")])

    def test_non_install_command(self):
        self.assertEqual(self.mi("npm run build"), [])


if __name__ == "__main__":
    unittest.main()
