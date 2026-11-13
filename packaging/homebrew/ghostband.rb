class Ghostband < Formula
  desc "Local accompaniment band that plays along with your song"
  homepage "https://github.com/intesarjawad/ghostband"
  # The release workflow replaces this digest with the tarball's real SHA-256.
  url "https://github.com/intesarjawad/ghostband/releases/download/v0.1.0/ghostband-engine-v0.1.0-macos-arm64.tar.gz"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "Apache-2.0"

  depends_on arch: :arm64
  depends_on :macos
  depends_on "uv"

  def install
    libexec.install "ghostband-engine", "mlx.metallib"
    wheel = "https://github.com/intesarjawad/ghostband/releases/download/v0.1.0/ghostband-0.1.0-py3-none-any.whl"
    (bin/"ghostband").write <<~SH
      #!/bin/sh
      export GHOSTBAND_ENGINE=#{libexec}/ghostband-engine
      UV=#{formula_opt_bin("uv")}/uv
      "$UV" tool run --offline --from #{wheel} ghostband --version >/dev/null 2>&1 || "$UV" tool run --from #{wheel} ghostband --version >/dev/null
      exec "$UV" tool run --offline --from #{wheel} ghostband "$@"
    SH
  end

  test do
    release = version.to_s[/\A\d+\.\d+\.\d+/]
    assert_match release, shell_output("#{bin}/ghostband --version")
    assert_match release, shell_output("#{libexec}/ghostband-engine --version")
  end
end
