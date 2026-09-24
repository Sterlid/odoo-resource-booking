/** @odoo-module **/

import { registry } from "@web/core/registry";
import { FormController } from "@web/views/form/form_controller";
import { formView } from "@web/views/form/form_view";

class SaveReturnFormController extends FormController {
    static template = "booking.SaveReturnForm";

    async saveButtonClicked(params = {}) {
        const saved = await super.saveButtonClicked(params);
        if (saved && !this.env.inDialog) {
            // Stay in the originating action to preserve its domain and filters.
            await this.actionService.switchView("list");
        }
        return saved;
    }
}

registry.category("views").add("booking_save_return_form", {
    ...formView,
    Controller: SaveReturnFormController,
});
