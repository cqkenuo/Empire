from __future__ import print_function

from builtins import object, str
from typing import Dict

from empire.server.core.db.models import Credential
from empire.server.core.module_models import EmpireModule
from empire.server.utils.module_util import handle_error_message


class Module(object):
    @staticmethod
    def generate(
        main_menu,
        module: EmpireModule,
        params: Dict,
        obfuscate: bool = False,
        obfuscation_command: str = "",
    ):
        # read in the common module source code
        script, err = main_menu.modulesv2.get_module_source(
            module_name=module.script_path,
            obfuscate=obfuscate,
            obfuscate_command=obfuscation_command,
        )

        if err:
            return handle_error_message(err)

        script_end = "\nInvoke-RunAs "

        # if a credential ID is specified, try to parse
        cred_id = params["CredID"]
        if cred_id != "":
            if not main_menu.credentials.is_credential_valid(cred_id):
                return handle_error_message("[!] CredID is invalid!")

            cred: Credential = main_menu.credentials.get_credentials(cred_id)

            if cred.credtype != "plaintext":
                return handle_error_message(
                    "[!] A CredID with a plaintext password must be used!"
                )

            if cred.domain != "":
                params["Domain"] = cred.domain
            if cred.username != "":
                params["UserName"] = cred.username
            if cred.password != "":
                params["Password"] = "'" + cred.password + "'"

        if (
            params["Domain"] == ""
            or params["UserName"] == ""
            or params["Password"] == ""
        ):
            return handle_error_message(
                "[!] Domain/UserName/Password or CredID required!"
            )

        for option, values in params.items():
            if option.lower() != "agent" and option.lower() != "credid":
                if values and values != "":
                    if values.lower() == "true":
                        # if we're just adding a switch
                        script_end += " -" + str(option)
                    else:
                        script_end += " -" + str(option) + " '" + str(values) + "'"

        script = main_menu.modulesv2.finalize_module(
            script=script,
            script_end=script_end,
            obfuscate=obfuscate,
            obfuscation_command=obfuscation_command,
        )
        return script
