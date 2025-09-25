import Layout from '@/components/Layout/Layout'
import { Box, Typography } from '@mui/material'
import UploadExcel from '@/components/Forms/UploadExcel'

export default function AdminHome() {
  return (
    <Layout pageTitle={'Admin Home Page'}>

        <Box paddingBottom={'2%'}>
            <Typography  variant='h2'>Configure Event</Typography>
            
                  
            <UploadExcel title='Upload Judges Excel Doc' path='/api/admin/judges'/>

            <UploadExcel title='Upload Posters Excel Doc' path='/api/admin/posters'/>


        </Box>

      

    </Layout>
  
  )
}