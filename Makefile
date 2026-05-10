.PHONY: install-global uninstall-global test doctor

install-global:
	./scripts/install-global-command.sh

uninstall-global:
	rm -f /Users/michaelpierre/.local/bin/toolkit

test:
	.venv/bin/pytest -q

doctor:
	.venv/bin/toolkit doctor

