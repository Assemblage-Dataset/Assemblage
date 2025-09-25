'use client';
import { yupResolver } from '@hookform/resolvers/yup';
import { Box, Button, Paper, TextField, Typography } from '@mui/material';
import React from 'react';
import { Controller, Form, SubmitHandler, useForm } from 'react-hook-form';
import { InferType, mixed, object } from 'yup';

// Props type
type Props = {
    title: string;
    path: string;
};

// Form inputs type
type FormInputs = InferType<typeof schema>;


// Yup schema validation
const schema = object({
    file: mixed()
        .required('A file is required')
        .test('fileFormat', 'Only Excel files are allowed', (value) => {
            // Ensure value is of type FileList and check file format
            if (value instanceof File) {
                return (
                    value.type === 'application/vnd.ms-excel' ||
                    value.type === 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                );
            }
            return false;
        })
        .test('fileSize', 'File size must be less than 5MB', (value) => {
            // Ensure value is of type FileList and check file size
   

            if (value instanceof File) {
                return value.size <= 5 * 1024 * 1024;  // 5MB
            }
            return false;
        }).nullable(),
});

export default function UploadExcel({ title, path }: Props) {


        const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
            const file = event.target.files?.[0];
            clearErrors('file')
            if (file) {
                const reader = new FileReader();
                reader.readAsDataURL(file);
                setValue('file', file); // Update form valu
            }
        };


    const formSubmitHandler: SubmitHandler<FormInputs> = async (data: FormInputs) => {
        if (data.file) {
            if (data.file instanceof File) {
                const formData = new FormData();
                formData.append('file', data.file);
        
                fetch(path, {
                    method: 'POST',
                    body: formData,
                })
                .then(response => {
                    if (!response.ok) { // Check if the response status is not in the 2xx range
                        throw new Error(`HTTP error! Status: ${response.status}`);
                    }
                    return response.json(); // Assuming the response is JSON
                })
                .then(data => {
                    alert("Excel document uploaded and processed!");
                    console.log(data);
                })
                .catch(error => {
                    alert("Failed to upload Excel document");
                    console.error('Error:', error);
                });
            } else {
                console.log('No file selected.');
            }
        }
        
   
    };

    const { control, handleSubmit, clearErrors,setValue, formState: {  } } = useForm<FormInputs>({
        resolver: yupResolver(schema), // Pass the correct resolver
    });

    return (
        <Box paddingBlock={'2%'}>
        <Paper>
            <Typography paddingTop='2%' variant='subtitle1'>{title}</Typography>
            
            <Form control={control}>
                <Controller
                    name='file'
                    key={'file'}
                    control={control}
                    render={({ fieldState }) => (
                            <TextField
                                onChange={handleFileChange}
                                type='file'
                                variant='outlined'
                                fullWidth
                                margin='normal'
                                error={!!fieldState?.error}
                                helperText={fieldState?.error?.message} // Display validation error
                            />
                    )}

                />
                <Button onClick={handleSubmit(formSubmitHandler)}>Submit</Button>
            </Form>
        </Paper>
        </Box>

    );
}
