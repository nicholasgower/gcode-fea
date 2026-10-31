# /// script
# [tool.orcaslicer.plugin]
# name = "Dummy Plugin"
# description = "Minimal plugin fixture for the sync helper tests."
# author = "GCode-FEA"
# version = "0.0.0"
# ///
import orca


class DummyCapability(orca.script.ScriptPluginCapabilityBase):
    def get_name(self):
        return "Dummy"

    def execute(self):
        return orca.ExecutionResult.success("dummy")


@orca.plugin
class DummyPlugin(orca.base):
    def register_capabilities(self):
        orca.register_capability(DummyCapability)
