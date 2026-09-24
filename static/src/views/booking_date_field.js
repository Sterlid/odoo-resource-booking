/** @odoo-module **/

import { today } from "@web/core/l10n/dates";
import { registry } from "@web/core/registry";
import { DateTimeField, dateField } from "@web/views/fields/datetime/datetime_field";

class BookingDateField extends DateTimeField {
    parseLimitDate(value) {
        if (value === "booking_notice") {
            return today().plus({ days: this.props.record.data.booking_rights ? 1 : 7 });
        }
        return super.parseLimitDate(value);
    }
}

registry.category("fields").add("booking_date_min_notice", {
    ...dateField,
    component: BookingDateField,
    extractProps(fieldInfo, dynamicInfo) {
        return {
            ...dateField.extractProps(fieldInfo, dynamicInfo),
            minDate: "booking_notice",
        };
    },
});
