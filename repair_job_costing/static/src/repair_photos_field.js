import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useFileViewer } from "@web/core/file_viewer/file_viewer_hook";
import { FileInput } from "@web/core/file_input/file_input";
import {
    Many2ManyBinaryField,
    many2ManyBinaryField,
} from "@web/views/fields/many2many_binary/many2many_binary_field";

// many2many_binary already uploads and links the files; this only changes the display to a
// thumbnail grid that opens the standard file viewer instead of downloading the file.
export class RepairPhotosField extends Many2ManyBinaryField {
    static template = "repair_job_costing.RepairPhotosField";
    static components = { FileInput };

    setup() {
        super.setup();
        this.store = useService("mail.store");
        this.fileViewer = useFileViewer();
    }

    openPhoto(file) {
        const photos = this.files.map((photo) =>
            this.store["ir.attachment"].insert({ id: photo.id, name: photo.name, mimetype: photo.mimetype })
        );
        this.fileViewer.open(photos.find((photo) => photo.id === file.id), photos);
    }
}

registry.category("fields").add("repair_photos", { ...many2ManyBinaryField, component: RepairPhotosField });
